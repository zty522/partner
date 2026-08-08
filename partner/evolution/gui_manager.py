"""GUI Manager — manages Partner GUI and external app lifecycle for self-evolution screenshots.

Ensures the Partner GUI (or an external app's window) is running before
screenshot capture, then captures the real window.

Architecture:
    GUIManager.ensure_partner_gui()
        → checks if Qt subprocess can render Partner GUI
        → if not, launches it via subprocess
        → waits for window readiness

    GUIManager.ensure_external_app(repo_url, target_dir, start_cmd)
        → git clone (if not exists)
        → install dependencies (npm install / pip install -e)
        → run start command in background
        → wait for window / process readiness
        → return capture path
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path("/mnt/e/work/partner")
WORKSPACE_ROOT = Path("/mnt/e/work/partner_workspace")
EXTERNAL_REPOS_DIR = WORKSPACE_ROOT / "external_repos"

# Default external repo configs
EXTERNAL_APPS: dict[str, dict[str, Any]] = {
    "hermes": {
        "repo_url": "https://github.com/fathah/hermes-desktop",
        "build_cmd": ["npx", "electron-vite", "build"],
        "serve_cmd": ["python3", "-m", "http.server", "5173", "--bind", "127.0.0.1"],
        "serve_dir": "out/renderer",
        "install_cmd": ["npm", "install"],
        "window_title": "Hermes Desktop",
        "port": 5173,
        "type": "static_vite",
    },
    "openclaw": {
        "repo_url": "https://github.com/wzdavid/openclaw-desktop",
        "build_cmd": ["npx", "vite", "build"],
        "serve_cmd": ["python3", "-m", "http.server", "5174", "--bind", "127.0.0.1"],
        "serve_dir": "dist",
        "install_cmd": ["npm", "install"],
        "window_title": "OpenClaw Desktop",
        "port": 5174,
        "type": "static_vite",
    },
}

# QT offscreen subprocess script template
_QT_SCREENSHOT_SCRIPT = """\
import sys, os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.pop("DISPLAY", None)
sys.path.insert(0, {project_root!r})
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
try:
    from shells.frontend.desktop_gui.modern.main_window import ModernMainWindow
    win = ModernMainWindow()
    win.resize({width}, {height})
    win.show()
    from PySide6.QtCore import QTimer, QEventLoop
    loop = QEventLoop()
    QTimer.singleShot({wait_ms}, loop.quit)
    loop.exec()
    pixmap = win.grab()
    pixmap.save({output_path!r})
    size = os.path.getsize({output_path!r})
    print("OK:" + str(pixmap.width()) + "x" + str(pixmap.height()) + ":" + str(size), file=sys.stderr)
except Exception as e:
    print("FAIL:" + str(e), file=sys.stderr)
    sys.exit(1)
"""

# ── Helper ───────────────────────────────────────────────────────────────────


def _screenshot_dir() -> str:
    """Use canonical get_screenshots_dir() from workspace utils."""
    from partner.utils.workspace import get_screenshots_dir
    return get_screenshots_dir()


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ═══════════════════════════════════════════════════════════════════════════════
# GUIManager
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class GUIResult:
    """Result of a GUI start/capture attempt."""
    success: bool
    output_path: str = ""
    window_title: str = ""
    error_message: str = ""
    elapsed_s: float = 0.0


class GUIManager:
    """Manages GUI process lifecycle for self-evolution screenshots."""

    def __init__(self, workspace: str | None = None):
        self._workspace = workspace or str(WORKSPACE_ROOT)
        self._screenshots_dir = _screenshot_dir()
        self._background_processes: dict[str, subprocess.Popen] = {}

    # ── Partner GUI ─────────────────────────────────────────────────────────

    async def ensure_partner_gui(
        self,
        width: int = 1280,
        height: int = 900,
        wait_ms: int = 3000,
        max_retries: int = 3,
    ) -> GUIResult:
        """Ensure Partner GUI can render and capture it.

        Launches a Qt Offscreen subprocess that:
        1. Creates QApplication
        2. Instantiates ModernMainWindow
        3. Renders offscreen
        4. Captures via win.grab()
        5. Saves PNG

        Returns:
            GUIResult with success=True + output_path on success,
            or success=False + error_message on failure.
        """
        output_path = os.path.join(self._screenshots_dir, f"partner_gui_{_ts()}.png")

        overall_start = time.monotonic()
        for attempt in range(1, max_retries + 1):
            start = time.monotonic()
            script = _QT_SCREENSHOT_SCRIPT.format(
                project_root=str(PROJECT_ROOT),
                width=width,
                height=height,
                wait_ms=wait_ms,
                output_path=output_path,
            )
            script_path = Path("/tmp") / f"qt_gui_{uuid.uuid4().hex[:8]}.py"
            try:
                script_path.write_text(script, encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    capture_output=True, text=True,
                    timeout=wait_ms // 1000 + 5,
                )
                elapsed = time.monotonic() - start

                if os.path.exists(output_path) and os.path.getsize(output_path) > 100 * 1024:
                    logger.info(
                        "[GUI] Partner GUI captured: %s (%d bytes, %.1fs)",
                        output_path, os.path.getsize(output_path), elapsed,
                    )
                    return GUIResult(
                        success=True, output_path=output_path,
                        window_title="Partner", elapsed_s=elapsed,
                    )

                # Check error
                err = result.stderr.strip() if result.stderr else "no output"
                if "FAIL:" in err:
                    cause = err.split("FAIL:")[-1].strip()[:200]
                else:
                    cause = err[:200]

                if os.path.exists(output_path):
                    logger.warning(
                        "[GUI] Attempt %d/%d: file exists but only %d bytes: %s",
                        attempt, max_retries, os.path.getsize(output_path), cause,
                    )
                else:
                    logger.warning(
                        "[GUI] Attempt %d/%d failed: %s",
                        attempt, max_retries, cause,
                    )

            except subprocess.TimeoutExpired:
                logger.warning("[GUI] Attempt %d/%d timed out after %ds", attempt, max_retries, wait_ms // 1000 + 5)
            except Exception as e:
                logger.warning("[GUI] Attempt %d/%d exception: %s", attempt, max_retries, e)
            finally:
                if script_path.exists():
                    script_path.unlink()

            if attempt < max_retries:
                await asyncio.sleep(2)

        return GUIResult(
            success=False,
            error_message=f"All {max_retries} attempts failed to capture Partner GUI",
            elapsed_s=time.monotonic() - overall_start,
        )

    # ── External App Management ────────────────────────────────────────────

    async def ensure_external_app(
        self, app_key: str, max_wait_s: int = 60, progress_callback=None,
    ) -> GUIResult:
        """Clone, install, and start an external app for screenshot.

        Args:
            app_key: Key in EXTERNAL_APPS dict ("hermes" or "openclaw").
            max_wait_s: Max seconds to wait for the app to start.

        Returns:
            GUIResult with path to screenshot on success.
        """
        config = EXTERNAL_APPS.get(app_key)
        if not config:
            return GUIResult(success=False, error_message=f"Unknown app: {app_key}")

        repo_dir = EXTERNAL_REPOS_DIR / app_key
        start_time = time.monotonic()

        # Step 1: Clone repo
        clone_result = await self._clone_repo(config["repo_url"], repo_dir)
        if not clone_result:
            return GUIResult(
                success=False,
                error_message=f"Failed to clone {app_key} from {config['repo_url']}",
                elapsed_s=time.monotonic() - start_time,
            )

        # Step 2: Check node_modules and install if needed
        has_node_modules = (repo_dir / "node_modules").exists()
        if not has_node_modules:
            install_result = await self._install_deps(repo_dir, config.get("install_cmd", ["npm", "install"]))
            if not install_result:
                return GUIResult(
                    success=False,
                    error_message=f"npm install failed for {app_key}. Try running it manually.",
                    elapsed_s=time.monotonic() - start_time,
                )

        # Step 3: Build the app (avoids Vite dev server WASM OOM)
        port = config.get("port", 5173)
        build_cmd = config.get("build_cmd")
        if build_cmd:
            if progress_callback:
                await progress_callback(f"🔨 构建 {app_key}（预计 30-60 秒）...")
            logger.info("[GUI] Building %s: %s", app_key, " ".join(build_cmd))
            build_result = await self._build_app(repo_dir, build_cmd, app_key)
            if not build_result:
                return GUIResult(
                    success=False,
                    error_message=f"Build failed for {app_key}",
                    elapsed_s=time.monotonic() - start_time,
                )

        # Step 4: Kill any stale HTTP servers on our ports before starting
        self._stop_app(app_key)
        import socket as _sk
        for _p in [port, port + 1]:
            _s = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
            if _s.connect_ex(("127.0.0.1", _p)) == 0:
                logger.info("[GUI] Port %d in use, will be freed by new process", _p)
            _s.close()

        serve_cmd = config.get("serve_cmd", ["python3", "-m", "http.server", str(port), "--bind", "127.0.0.1"])
        serve_dir = config.get("serve_dir")
        start_result = await self._start_app(
            app_key, repo_dir, serve_cmd,
            port=port, max_wait_s=15, use_xvfb=False,
            app_cwd_override=serve_dir,
        )
        if not start_result:
            # Log diagnostics: read the startup log
            log_path = EXTERNAL_REPOS_DIR / f"{app_key}_startup.log"
            if log_path.exists():
                log_tail = open(log_path).read()[-500:]
                logger.info("[GUI] %s startup log tail:\n%s", app_key, log_tail)
            return GUIResult(
                success=False,
                error_message=f"Failed to start {app_key} (Electron app in headless WSL may need display)",
                elapsed_s=time.monotonic() - start_time,
            )

        # Small delay to let HTTP server stabilize
        await asyncio.sleep(1)

        # Step 5: Capture screenshot
        output_path = os.path.join(
            self._screenshots_dir, f"{app_key}_ui_{_ts()}.png"
        )
        capture_result = await self._capture_web_app(port, output_path, timeout=max_wait_s)
        if capture_result:
            logger.info(
                "[GUI] %s UI captured: %s (%d bytes, %.1fs)",
                app_key, output_path, os.path.getsize(output_path),
                time.monotonic() - start_time,
            )
            return GUIResult(
                success=True, output_path=output_path,
                window_title=config.get("window_title", app_key),
                elapsed_s=time.monotonic() - start_time,
            )

        # Try PrintWindow capture as fallback
        pw_path = os.path.join(
            self._screenshots_dir, f"pw_{app_key}_{_ts()}.png"
        )
        pw_result = self._capture_printwindow(config.get("window_title", app_key), pw_path)
        if pw_result:
            return GUIResult(
                success=True, output_path=pw_path,
                window_title=config.get("window_title", app_key),
                elapsed_s=time.monotonic() - start_time,
            )

        return GUIResult(
            success=False,
            error_message=f"App {app_key} started but could not capture screenshot",
            elapsed_s=time.monotonic() - start_time,
        )

    async def _clone_repo(self, repo_url: str, target_dir: Path) -> bool:
        """Clone a git repository if not already present."""
        if target_dir.exists() and (target_dir / ".git").exists():
            logger.info("[GUI] Repo already exists at %s, pulling...", target_dir)
            try:
                subprocess.run(
                    ["git", "-C", str(target_dir), "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=30,
                )
                return True
            except Exception:
                return True  # Existing repo is fine even if pull fails

        logger.info("[GUI] Cloning %s -> %s...", repo_url, target_dir)
        EXTERNAL_REPOS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["git", "clone", repo_url, str(target_dir)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning("[GUI] Clone failed: %s", result.stderr[:200])
                return False
            logger.info("[GUI] Clone succeeded: %s", target_dir)
            return True
        except subprocess.TimeoutExpired:
            logger.warning("[GUI] Clone timed out after 120s")
            return False
        except Exception as e:
            logger.warning("[GUI] Clone exception: %s", e)
            return False

    async def _install_deps(self, repo_dir: Path, install_cmd: list[str]) -> bool:
        """Install project dependencies."""
        logger.info("[GUI] Installing dependencies in %s...", repo_dir)
        try:
            result = subprocess.run(
                install_cmd,
                cwd=str(repo_dir),
                capture_output=True, text=True, timeout=180,
            )
            if result.returncode != 0:
                # npm install can have warnings that are not fatal
                if "ERR!" in result.stderr or "error" in result.stderr.lower():
                    logger.warning("[GUI] Install had errors: %s", result.stderr[:300])
                    return False
            logger.info("[GUI] Dependencies installed")
            return True
        except subprocess.TimeoutExpired:
            logger.warning("[GUI] Install timed out after 180s")
            return False
        except Exception as e:
            logger.warning("[GUI] Install exception: %s", e)
            return False

    async def _build_app(self, repo_dir: Path, build_cmd: list[str], app_key: str) -> bool:
        """Build the app (avoids Vite dev server WASM OOM during screenshot).

        Skips build if output already exists (from a previous run).
        """
        # Check if build output already exists
        serve_dir_config = EXTERNAL_APPS.get(app_key, {}).get("serve_dir", "")
        if serve_dir_config:
            build_output = repo_dir / serve_dir_config / "index.html"
            if build_output.exists():
                logger.info("[GUI] Build output exists for %s, skipping rebuild", app_key)
                return True
        try:
            result = await asyncio.to_thread(
                lambda: subprocess.run(
                    build_cmd,
                    cwd=str(repo_dir),
                    capture_output=True, text=True, timeout=120,
                    env={**os.environ, "NODE_OPTIONS": "--max-old-space-size=2048"},
                )
            )
            if result.returncode == 0:
                logger.info("[GUI] Build succeeded for %s", app_key)
                return True
            else:
                logger.warning("[GUI] Build failed for %s: %s", app_key, result.stderr[:300])
                return False
        except subprocess.TimeoutExpired:
            logger.warning("[GUI] Build timed out for %s after 120s", app_key)
            return False
        except Exception as e:
            logger.warning("[GUI] Build exception for %s: %s", app_key, e)
            return False

    async def _start_app(
        self, app_key: str, repo_dir: Path, start_cmd: list[str],
        port: int = 5173, max_wait_s: int = 60, use_xvfb: bool = False,
        app_cwd_override: str | None = None,
    ) -> bool:
        """Start the app in background and wait for readiness.

        If use_xvfb is True and xvfb-run is available, wraps the command
        in xvfb-run for headless display rendering.
        """
        # Kill any existing process for this app
        self._stop_app(app_key)

        # Check xvfb availability
        xvfb_available = os.path.exists("/usr/bin/xvfb-run") or shutil.which("xvfb-run") is not None
        if use_xvfb and not xvfb_available:
            logger.warning("[GUI] xvfb-run not available, trying without virtual display")
            use_xvfb = False

        actual_cmd = start_cmd[:]
        # Set app-specific environment variables
        app_env = {**os.environ, "BROWSER": "none"}
        if app_key == "hermes":
            app_env["HERMES_DESKTOP_RENDERER_PORT"] = str(port)
            logger.info("[GUI] Setting HERMES_DESKTOP_RENDERER_PORT=%d", port)

        log_path = EXTERNAL_REPOS_DIR / f"{app_key}_startup.log"
        logger.info("[GUI] Starting %s on port %d...", app_key, port)
        config = EXTERNAL_APPS.get(app_key, {})
        app_cwd = str(repo_dir)
        rel_cwd = config.get("cwd", "")
        if app_cwd_override:
            app_cwd = str(repo_dir / app_cwd_override)
            logger.info("[GUI]   cwd (override): %s", app_cwd)
        elif rel_cwd:
            app_cwd = str(repo_dir / rel_cwd)
            logger.info("[GUI]   cwd: %s", app_cwd)

        start_ts = time.monotonic()
        try:
            proc = subprocess.Popen(
                actual_cmd,
                cwd=app_cwd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=app_env,
                preexec_fn=os.setpgrp,
            )
            self._background_processes[app_key] = proc
            logger.info("[GUI] %s started (PID %d)", app_key, proc.pid)

            # Wait for port to be ready
            import socket
            deadline = time.monotonic() + max_wait_s
            while time.monotonic() < deadline:
                await asyncio.sleep(1)
                # Check if process is still alive
                if proc.poll() is not None:
                    log_content = ""
                    if log_path.exists():
                        log_content = log_path.read_text(errors="replace")[-500:]
                    logger.warning(
                        "[GUI] %s exited prematurely (code %d). Log:\n%s",
                        app_key, proc.returncode, log_content,
                    )
                    return False
                # Check if port is listening
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                result = sock.connect_ex(("127.0.0.1", port))
                sock.close()
                if result == 0:
                    logger.info("[GUI] %s ready on port %d (%.1fs)", app_key, port, time.monotonic() - start_ts)
                    return True

            logger.warning("[GUI] %s did not start within %ds on port %d", app_key, max_wait_s, port)
            # Read log for diagnostics
            if log_path.exists():
                log_content = log_path.read_text(errors="replace")[-500:]
                logger.info("[GUI] %s startup log:\n%s", app_key, log_content)
            return False
        except Exception as e:
            logger.warning("[GUI] Failed to start %s: %s", app_key, e)
            return False

    def _stop_app(self, app_key: str) -> None:
        """Stop a background app if running."""
        proc = self._background_processes.pop(app_key, None)
        if proc and proc.poll() is None:
            logger.info("[GUI] Stopping %s (PID %d)...", app_key, proc.pid)
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            except Exception:
                pass
            logger.info("[GUI] %s stopped", app_key)

    def stop_all(self) -> None:
        """Stop all background apps."""
        for app_key in list(self._background_processes.keys()):
            self._stop_app(app_key)

    async def _capture_web_app(self, port: int, output_path: str, timeout: int = 30) -> bool:
        """Capture a web app via Playwright or requests+html2image."""
        # Method 1: Playwright
        pw_script = "\n".join([
            'import asyncio, sys',
            'async def main():',
            '    try:',
            '        from playwright.async_api import async_playwright',
            '    except ImportError:',
            '        print("FAIL:playwright not installed")',
            '        sys.exit(1)',
            '    async with async_playwright() as p:',
            f'        browser = await p.chromium.launch(headless=True)',
            f'        page = await browser.new_page(viewport={{"width": 1280, "height": 800}})',
            f'        try:',
            f'            await page.goto(f"http://127.0.0.1:{port}", timeout={timeout * 1000})',
            f'            await page.wait_for_load_state("networkidle", timeout=10000)',
            f'        except Exception as e:',
            f'            print(f"FAIL:goto failed: {{e}}")',
            f'            await browser.close()',
            f'            sys.exit(1)',
            f'        await page.screenshot(path={output_path!r})',
            f'        print("OK:" + str(os.path.getsize({output_path!r})) if os.path.exists({output_path!r}) else "FAIL:no file")',
            f'        await browser.close()',
            'asyncio.run(main())',
        ])
        script_path = Path("/tmp") / f"pw_{uuid.uuid4().hex[:8]}.py"
        try:
            script_path.write_text(pw_script)
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True, text=True, timeout=timeout + 10,
            )
            if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
                logger.info("[GUI] Playwright capture: %s (%d bytes)", output_path, os.path.getsize(output_path))
                return True
            err = result.stderr.strip()[:200] if result.stderr else "no output"
            logger.debug("[GUI] Playwright capture failed: %s", err)
        except Exception as e:
            logger.debug("[GUI] Playwright error: %s", e)
        finally:
            if script_path.exists():
                script_path.unlink()

        return False

    def _capture_printwindow(self, window_title: str, output_path: str) -> bool:
        """Capture a window by title via PowerShell PrintWindow (Windows only)."""
        pw = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        if not os.path.exists(pw):
            return False

        win_out = "C:\\temp\\gui_ss_" + uuid.uuid4().hex[:8] + ".png"
        ps_script = '\n'.join([
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
        ps_file = Path("/tmp") / f"ps_{uuid.uuid4().hex[:8]}.ps1"
        try:
            ps_file.write_text(ps_script, encoding="utf-8")
            result = subprocess.run(
                [pw, "-NoProfile", "-ExecutionPolicy", "Bypass", str(ps_file)],
                capture_output=True, text=True, timeout=15,
            )
            win_path = win_out.replace("\\", "/").replace("C:", "/mnt/c")
            if os.path.exists(win_path):
                shutil.move(win_path, output_path)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 500:
                    logger.info("[GUI] PrintWindow capture: %s (%d bytes)", output_path, os.path.getsize(output_path))
                    return True
        except Exception as e:
            logger.debug("[GUI] PrintWindow error: %s", e)
        finally:
            if ps_file.exists():
                ps_file.unlink()
        return False


# ── Convenience async function ───────────────────────────────────────────────


async def capture_all_screenshots(
    workspace: str | None = None,
    progress_callback=None,
) -> dict[str, str]:
    """Capture all relevant screenshots using standalone subprocess.

    The standalone script runs as a subprocess isolated from Partner's event loop,
    so it avoids event-loop blocking issues with Node.js servers and Playwright.

    Returns:
        dict with keys: "partner_gui", "hermes", "openclaw"
        Values are paths to screenshots or "" if failed.
    """
    results: dict[str, str] = {
        "partner_gui": "", "hermes": "", "openclaw": "",
    }

    if progress_callback:
        await progress_callback("📸 启动独立截图进程（避免事件循环干扰）...")

    try:
        import subprocess as _sp, json as _j
        _r = _sp.run(
            [sys.executable, "-m", "partner.evolution.standalone_screenshots"],
            capture_output=True, text=True, timeout=120,
        )
        for _line in _r.stdout.split("\n"):
            if _line.strip().startswith("{"):
                _data = _j.loads(_line.strip())
                if "results" in _data:
                    for _k, _v in _data["results"].items():
                        results[_k] = _v
                    break
    except Exception as _se:
        logger.warning("[GUI] Standalone screenshot failed: %s", _se)

    counts = sum(1 for v in results.values() if v)
    if progress_callback:
        await progress_callback(f"📸 截图完成: {counts}/3 成功")
        for key, path in results.items():
            if path:
                await progress_callback(f"  ✅ {key}: {os.path.basename(path)} ({os.path.getsize(path)//1024}KB)")
            else:
                await progress_callback(f"  ❌ {key}: 截图未生成")

    return results
