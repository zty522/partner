"""Partner Virtual Display Capture — background window screenshots from WSL via PowerShell.

Uses DWM (DwmGetWindowAttribute) for accurate window bounds, with
GetWindowRect fallback. Supports multi-monitor setups.
"""

import subprocess, os, shutil, time, logging

logger = logging.getLogger(__name__)
CAPTURE_PS1 = r"C:\PartnerTools\capture.ps1"
ACTIONS_PS1 = r"C:\PartnerTools\actions.ps1"


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


def _parse_ps_output(output: str) -> dict:
    """Parse structured output from capture.ps1 (semicolon-delimited)."""
    result = {"found": 0, "states": [], "captured": None, "captured_size": 0, "errors": []}
    for line in output.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith("NOT_FOUND;"):
            result["not_found_title"] = line[10:]
        elif line.startswith("FOUND;"):
            result["found"] = int(line[6:])
        elif line.startswith("STATE;"):
            parts = line[6:].split(";", 2)
            if len(parts) >= 2:
                result["states"].append({"pid": parts[0], "state": parts[1],
                                         "title": parts[2] if len(parts) > 2 else ""})
        elif line.startswith("CAPTURED;"):
            parts = line[9:].split(";")
            result["captured"] = parts[0]
            result["captured_size"] = int(parts[1]) if len(parts) > 1 else 0
        elif line.startswith("FAILED;"):
            result["errors"].append(line[7:])
    return result


def list_windows(timeout: int = 15) -> dict:
    """List all visible windows."""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", CAPTURE_PS1, "-ListWindows"],
            capture_output=True, text=True, timeout=timeout
        )
        windows = []
        for line in r.stdout.strip().split('\n'):
            line = line.strip()
            if line.startswith("WINDOW;"):
                parts = line[7:].split(";")
                if len(parts) >= 6:
                    windows.append({
                        "title": parts[0].strip(),
                        "pid": int(parts[1]),
                        "x": int(parts[2]), "y": int(parts[3]),
                        "w": int(parts[4]), "h": int(parts[5]),
                    })
        return {"ok": True, "count": len(windows), "windows": windows}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "列出窗口超时"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def capture_window(window_title: str, output_dir: str = None, timeout: int = 30) -> dict:
    """Capture a specific window by title (partial match).

    Uses DWM extended frame bounds for accurate dimensions. Does NOT
    move, restore, or focus the window.

    Returns:
        {"ok": True, "path": "...", "size": N, "state": "normal"}
        {"ok": False, "error": "...", "reason": "not_found|minimized|..."}
    """
    if not output_dir:
        output_dir = _get_screenshots_dir()
    os.makedirs(output_dir, exist_ok=True)

    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in window_title)[:40]
    ts = time.strftime("%Y%m%d_%H%M%S")
    win_output = r"C:/temp/partner_" + safe_name + "_" + str(os.getpid()) + ".png"

    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", CAPTURE_PS1, "-WindowTitle", window_title, "-Output", win_output],
            capture_output=True, text=True, timeout=timeout
        )
        parsed = _parse_ps_output(r.stdout + r.stderr)

        # Not found
        if "not_found_title" in parsed:
            return {"ok": False,
                    "error": f"未找到窗口「{parsed['not_found_title']}」（请确认软件已打开）",
                    "reason": "not_found"}

        # Success
        if parsed["captured"] and parsed["captured_size"] > 0:
            wsl_tmp = parsed["captured"].replace("\\", "/").replace("C:", "/mnt/c")
            if os.path.exists(wsl_tmp):
                final_path = os.path.join(output_dir, f"win_{safe_name}_{ts}.png")
                shutil.move(wsl_tmp, final_path)
                sz = os.path.getsize(final_path)
                # Window state from first normal window
                state = "normal"
                for ws in parsed["states"]:
                    if ws["state"] != "minimized":
                        state = ws["state"]
                        break
                return {"ok": True, "path": final_path, "size": sz, "state": state}
            return {"ok": False, "error": "截图文件未生成", "reason": "file_missing"}

        # All minimized
        if parsed["found"] > 0 and all(ws["state"] == "minimized" for ws in parsed["states"]):
            return {"ok": False,
                    "error": f"「{window_title}」已最小化到托盘，请先打开窗口",
                    "reason": "minimized", "state": "minimized"}

        # Errors
        if parsed["errors"]:
            return {"ok": False, "error": "; ".join(parsed["errors"]), "reason": "capture_error"}
        if parsed["found"] == 0:
            return {"ok": False, "error": f"未找到窗口「{window_title}」", "reason": "not_found"}
        return {"ok": False,
                "error": f"捕获失败（找到 {parsed['found']} 个进程但均无法截图）",
                "reason": "unknown"}

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"截图超时（{timeout}秒）", "reason": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e), "reason": "exception"}


def capture_fullscreen(output_dir: str = None, timeout: int = 15) -> dict:
    """Capture all monitors combined."""
    if not output_dir:
        output_dir = _get_screenshots_dir()
    os.makedirs(output_dir, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    win_output = r"C:/temp/partner_full_" + str(os.getpid()) + ".png"

    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", CAPTURE_PS1, "-FullScreen", "-Output", win_output],
            capture_output=True, text=True, timeout=timeout
        )
        parsed = _parse_ps_output(r.stdout + r.stderr)

        if parsed["captured"] and parsed["captured_size"] > 0:
            wsl_tmp = parsed["captured"].replace("\\", "/").replace("C:", "/mnt/c")
            if os.path.exists(wsl_tmp):
                final_path = os.path.join(output_dir, f"winscr_full_{ts}.png")
                shutil.move(wsl_tmp, final_path)
                return {"ok": True, "path": final_path, "size": os.path.getsize(final_path)}
            return {"ok": False, "error": "截图文件未生成", "reason": "file_missing"}

        return {"ok": False, "error": "全屏截图失败", "reason": "capture_error"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "全屏截图超时", "reason": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e), "reason": "exception"}


# ── Extended operations ────────────────────────────────────────────

def click(x: int, y: int, timeout: int = 10) -> dict:
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", ACTIONS_PS1, "-Action", "click", "-Target", f"{x},{y}"],
            capture_output=True, text=True, timeout=timeout)
        if "CLICKED:" in r.stdout:
            return {"ok": True, "x": x, "y": y}
        return {"ok": False, "error": r.stdout[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_keys(keys: str, timeout: int = 10) -> dict:
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", ACTIONS_PS1, "-Action", "type", "-Target", keys],
            capture_output=True, text=True, timeout=timeout)
        if "KEYS_SENT:" in r.stdout:
            return {"ok": True, "keys": keys}
        return {"ok": False, "error": r.stdout[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def scroll(amount: int = 120, timeout: int = 10) -> dict:
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", ACTIONS_PS1, "-Action", "scroll", "-Target", str(amount)],
            capture_output=True, text=True, timeout=timeout)
        if "SCROLLED:" in r.stdout:
            return {"ok": True, "amount": amount}
        return {"ok": False, "error": r.stdout[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def launch(app_path: str, timeout: int = 15) -> dict:
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", ACTIONS_PS1, "-Action", "launch", "-Target", app_path],
            capture_output=True, text=True, timeout=timeout)
        if "LAUNCHED:" in r.stdout:
            return {"ok": True, "app": app_path}
        return {"ok": False, "error": r.stdout[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
