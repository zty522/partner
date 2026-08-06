"""partner/v2/control.py — 鼠标、键盘、剪贴板、应用操控原子操作。

每个 atomic_XXX(ctx, params) -> dict 返回 {"success": bool, "result": Any, "error": str | None}。

依赖:
  - pyautogui: 鼠标/键盘操作
  - pyperclip: 剪贴板读写
  - PIL: 图像处理（剪贴板图片）
  - pygetwindow: 窗口操作
  - subprocess: 进程管理
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Lazy import helpers ──

def _import_pyautogui():
    """Lazy import pyautogui."""
    import pyautogui
    pyautogui.FAILSAFE = True
    return pyautogui


def _import_pyperclip():
    """Lazy import pyperclip."""
    import pyperclip
    return pyperclip


def _import_pil():
    """Lazy import PIL.Image / ImageGrab."""
    from PIL import Image, ImageGrab
    return Image, ImageGrab


def _import_pygetwindow():
    """Lazy import pygetwindow."""
    import pygetwindow as gw
    return gw


# ── Result helpers ──

def _ok(result: Any = None) -> dict:
    return {"success": True, "result": result, "error": None}


def _err(msg: str) -> dict:
    logger.error(msg)
    return {"success": False, "result": None, "error": msg}


# ════════════════════════════════════════════════════════════════
# 1. Mouse Move
# ════════════════════════════════════════════════════════════════

def atomic_mouse_move(ctx: dict, params: dict) -> dict:
    """Move the mouse to absolute screen coordinate (x, y).

    Params:
        x (int): target x-coordinate.
        y (int): target y-coordinate.
        duration (float, optional): number of seconds to take for motion. Default 0.2.
    """
    try:
        pg = _import_pyautogui()
        x = int(params["x"])
        y = int(params["y"])
        duration = float(params.get("duration", 0.2))
        pg.moveTo(x, y, duration=duration)
        return _ok({"x": x, "y": y})
    except Exception as e:
        return _err(f"Mouse move failed: {e}")


# ════════════════════════════════════════════════════════════════
# 2. Mouse Click
# ════════════════════════════════════════════════════════════════

def atomic_mouse_click(ctx: dict, params: dict) -> dict:
    """Click at (x, y) or current position.

    Params:
        x (int, optional): x-coordinate. Omit to click at current position.
        y (int, optional): y-coordinate. Omit to click at current position.
        button (str, optional): 'left', 'right', or 'middle'. Default 'left'.
        clicks (int, optional): number of clicks. Default 1.
    """
    try:
        pg = _import_pyautogui()
        x = params.get("x")
        y = params.get("y")
        button = params.get("button", "left")
        clicks = int(params.get("clicks", 1))

        if x is not None and y is not None:
            pg.click(int(x), int(y), clicks=clicks, button=button)
        else:
            pg.click(clicks=clicks, button=button)

        return _ok({"button": button, "clicks": clicks, "x": x, "y": y})
    except Exception as e:
        return _err(f"Mouse click failed: {e}")


# ════════════════════════════════════════════════════════════════
# 3. Mouse Drag
# ════════════════════════════════════════════════════════════════

def atomic_mouse_drag(ctx: dict, params: dict) -> dict:
    """Drag from (start_x, start_y) to (end_x, end_y).

    Params:
        start_x (int): starting x-coordinate.
        start_y (int): starting y-coordinate.
        end_x (int): ending x-coordinate.
        end_y (int): ending y-coordinate.
        duration (float, optional): duration in seconds. Default 0.5.
    """
    try:
        pg = _import_pyautogui()
        sx = int(params["start_x"])
        sy = int(params["start_y"])
        ex = int(params["end_x"])
        ey = int(params["end_y"])
        duration = float(params.get("duration", 0.5))

        pg.moveTo(sx, sy)
        pg.drag(ex - sx, ey - sy, duration=duration)

        return _ok({"from": (sx, sy), "to": (ex, ey)})
    except Exception as e:
        return _err(f"Mouse drag failed: {e}")


# ════════════════════════════════════════════════════════════════
# 4. Mouse Scroll
# ════════════════════════════════════════════════════════════════

def atomic_mouse_scroll(ctx: dict, params: dict) -> dict:
    """Scroll the mouse wheel.

    Params:
        clicks (int): number of scroll clicks. Positive = scroll up, negative = scroll down.
        x (int, optional): x-coordinate to scroll at (moves mouse there first).
        y (int, optional): y-coordinate to scroll at.
    """
    try:
        pg = _import_pyautogui()
        clicks = int(params["clicks"])
        x = params.get("x")
        y = params.get("y")

        if x is not None and y is not None:
            pg.moveTo(int(x), int(y))

        pg.scroll(clicks)

        return _ok({"clicks": clicks, "x": x, "y": y})
    except Exception as e:
        return _err(f"Mouse scroll failed: {e}")


# ════════════════════════════════════════════════════════════════
# 5. Keyboard Type
# ════════════════════════════════════════════════════════════════

def atomic_keyboard_type(ctx: dict, params: dict) -> dict:
    """Type text at the currently focused element.

    Params:
        text (str): the text to type.
        interval (float, optional): seconds between each key press. Default 0.05.
    """
    try:
        pg = _import_pyautogui()
        text = str(params["text"])
        interval = float(params.get("interval", 0.05))

        pg.write(text, interval=interval)

        return _ok({"typed_length": len(text)})
    except Exception as e:
        return _err(f"Keyboard type failed: {e}")


# ════════════════════════════════════════════════════════════════
# 6. Keyboard Press (combo)
# ════════════════════════════════════════════════════════════════

def atomic_keyboard_press(ctx: dict, params: dict) -> dict:
    """Press and release a combination of keys.

    Params:
        keys (list[str]): list of key names, e.g. ['ctrl', 'c'].
    """
    try:
        pg = _import_pyautogui()
        keys = list(params["keys"])

        pg.hotkey(*keys)

        return _ok({"keys": keys})
    except Exception as e:
        return _err(f"Keyboard press failed: {e}")


# ════════════════════════════════════════════════════════════════
# 7. Clipboard Get
# ════════════════════════════════════════════════════════════════

def atomic_clipboard_get(ctx: dict, params: dict) -> dict:
    """Read the system clipboard.

    Params:
        format (str, optional): 'text' (default) or 'image'.
    """
    try:
        fmt = params.get("format", "text")

        if fmt == "image":
            Image, ImageGrab = _import_pil()
            img = ImageGrab.grabclipboard()
            if img is None:
                return _err("No image found on clipboard")
            return _ok({"format": "image", "size": img.size, "mode": img.mode})
        else:
            pc = _import_pyperclip()
            content = pc.paste()
            return _ok({"format": "text", "content": content})
    except Exception as e:
        return _err(f"Clipboard get failed: {e}")


# ════════════════════════════════════════════════════════════════
# 8. Clipboard Set
# ════════════════════════════════════════════════════════════════

def atomic_clipboard_set(ctx: dict, params: dict) -> dict:
    """Write content to the system clipboard.

    Params:
        content (str): the text content to set.
        format (str, optional): 'text' (default). Image not yet supported for set.
    """
    try:
        fmt = params.get("format", "text")

        if fmt == "image":
            return _err("Clipboard set for image format is not yet implemented")

        pc = _import_pyperclip()
        pc.copy(str(params["content"]))
        return _ok({"format": "text", "length": len(str(params["content"]))})
    except Exception as e:
        return _err(f"Clipboard set failed: {e}")


# ════════════════════════════════════════════════════════════════
# 9. App Launch
# ════════════════════════════════════════════════════════════════

def atomic_app_launch(ctx: dict, params: dict) -> dict:
    """Launch an application.

    Params:
        command (str): executable name or path.
        args (list[str], optional): list of command-line arguments.
        wait (bool, optional): whether to wait for the process to exit. Default False.
    """
    try:
        command = str(params["command"])
        args = params.get("args") or []
        wait = bool(params.get("wait", False))

        cmd_list = [command] + list(args)
        proc = subprocess.Popen(
            cmd_list,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if wait:
            proc.wait()

        return _ok({
            "command": command,
            "args": args,
            "pid": proc.pid,
            "wait": wait,
        })
    except Exception as e:
        return _err(f"App launch failed: {e}")


# ════════════════════════════════════════════════════════════════
# 10. App Close
# ════════════════════════════════════════════════════════════════

def atomic_app_close(ctx: dict, params: dict) -> dict:
    """Close a window by title or process name.

    Params:
        target (str): window title (substring match) or process name.
    """
    try:
        target = str(params["target"])

        # Try pygetwindow first
        try:
            gw = _import_pygetwindow()
            windows = gw.getWindowsWithTitle(target)
            if windows:
                for w in windows:
                    w.close()
                return _ok({"method": "pygetwindow", "target": target, "closed": len(windows)})
        except Exception:
            pass

        # Fallback: taskkill (Windows) / pkill (Unix)
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", target] if sys.platform == "win32" else ["pkill", "-f", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            return _ok({"method": "process_kill", "target": target})
        except subprocess.CalledProcessError:
            pass

        return _err(f"Could not close target: {target}")
    except Exception as e:
        return _err(f"App close failed: {e}")


# ════════════════════════════════════════════════════════════════
# 11. App Send Keys
# ════════════════════════════════════════════════════════════════

def atomic_app_send_keys(ctx: dict, params: dict) -> dict:
    """Send keystrokes to a specific window.

    Params:
        target (str): window title (substring match) to focus.
        keys (str): the keys to type after focusing the window.
        interval (float, optional): seconds between key presses. Default 0.05.
    """
    try:
        target = str(params["target"])
        keys = str(params["keys"])
        interval = float(params.get("interval", 0.05))
        pg = _import_pyautogui()

        # Activate the window via pygetwindow
        try:
            gw = _import_pygetwindow()
            windows = gw.getWindowsWithTitle(target)
            if windows:
                win = windows[0]
                win.activate()
                time.sleep(0.3)  # brief pause for window to come to foreground
        except Exception as exc:
            logger.warning("Could not focus window '%s': %s", target, exc)

        pg.write(keys, interval=interval)
        return _ok({"target": target, "typed_length": len(keys)})
    except Exception as e:
        return _err(f"App send_keys failed: {e}")


# ════════════════════════════════════════════════════════════════
# 12–15: Windows GUI control via win-gui-test-skill (pywinauto)
# ════════════════════════════════════════════════════════════════

_WIN_GUI_CLI = "/mnt/e/work/win-gui-test-skill/scripts/cli.py"


def _run_win_gui(cmd_args: list[str], timeout: int = 30) -> dict:
    """Run a win-gui-test-skill CLI command via Windows PowerShell.

    Returns {"ok": bool, "stdout": str, "stderr": str, "data": dict|None}
    """
    import subprocess as _sp, json as _json, os as _os
    if not _os.path.exists(_WIN_GUI_CLI):
        return {"ok": False, "error": f"win-gui-test-skill not found at {_WIN_GUI_CLI}"}
    _win_path = _WIN_GUI_CLI.replace("/mnt/c/", "C:/").replace("/mnt/e/", "E:/")
    if _win_path.startswith("/mnt/"):
        _drive = _win_path[5]  # e.g. "e"
        _rest = _win_path[7:]
        _win_path = f"{_drive.upper()}:/{_rest}"
    _ps_cmd = f"python '{_win_path}' {' '.join(cmd_args)}"
    _win_python = "/mnt/c/Windows/py.exe"  # Windows Python launcher
    if _os.path.exists(_win_python):
        _runner = [_win_python, "-3"]
    else:
        _runner = ["powershell.exe", "-NoProfile", "-Command", _ps_cmd]
    try:
        _r = _sp.run(_runner + ([_ps_cmd] if _runner[0] == "powershell.exe" else cmd_args),
                     capture_output=True, text=True, timeout=timeout)
        _stdout = _r.stdout.strip()
        _data = None
        # Try to parse JSON from stdout
        if _stdout:
            _json_start = _stdout.find("{")
            if _json_start >= 0:
                try:
                    _data = _json.loads(_stdout[_json_start:])
                except Exception:
                    pass
        return {"ok": _r.returncode == 0, "stdout": _stdout[:2000],
                "stderr": _r.stderr[:500], "data": _data, "returncode": _r.returncode}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def atomic_app_list_windows(ctx, params):
    """List all visible Windows GUI windows via pywinauto.

    Params: filter (optional str) — substring to filter window titles.
    Returns: {ok, windows: [{title, handle, rect}], count}
    """
    r = _run_win_gui(["list-all"])
    if r.get("ok") and r.get("stdout"):
        import re as _re
        _filter = (params.get("filter") or "").strip().lower()
        _lines = r["stdout"].split("\n")
        _windows = []
        for _l in _lines:
            _m = _re.search(r"(.+?)\s+\((\d+)\)\s+(-?\d+),(-?\d+)\s+(\d+)x(\d+)", _l)
            if _m:
                _title = _m.group(1).strip()
                if _filter and _filter not in _title.lower():
                    continue
                _windows.append({
                    "title": _title, "handle": int(_m.group(2)),
                    "rect": {"x": int(_m.group(3)), "y": int(_m.group(4)),
                             "w": int(_m.group(5)), "h": int(_m.group(6))}
                })
        return {"ok": True, "windows": _windows, "count": len(_windows)}
    return {"ok": False, "windows": [], "error": r.get("error", "no output")}


def atomic_app_click_element(ctx, params):
    """Click a Windows GUI element by window title and element name.

    Params: target (str) — window title; element (str) — element name to click.
    Returns: {ok, details}
    """
    _target = params.get("target", "")
    _element = params.get("element", "")
    if not _target or not _element:
        return {"ok": False, "error": "target and element params required"}
    r = _run_win_gui(["click", _target, _element])
    return {"ok": r.get("ok"), "details": r.get("stdout", r.get("error", ""))[:200]}


def atomic_app_screenshot_window(ctx, params):
    """Take a screenshot of a specific Windows window.

    Params: target (str) — window title; save_path (optional).
    Uses win-gui-test-skill which captures via pywinauto without flashing.
    """
    _target = params.get("target", "")
    if not _target:
        return {"ok": False, "error": "target param required"}
    _save = params.get("save_path", "")
    _args = ["screenshot", _target]
    if _save:
        _args.extend(["--out-dir", _save])
    r = _run_win_gui(_args)
    return {"ok": r.get("ok"), "details": r.get("stdout", r.get("error", ""))[:200]}


def atomic_app_list_elements(ctx, params):
    """List UI elements of a Windows window.

    Params: target (str) — window title.
    Returns: {ok, elements: [{name, type, rect}], count}
    """
    _target = params.get("target", "")
    if not _target:
        return {"ok": False, "error": "target param required"}
    r = _run_win_gui(["list-elements", _target])
    _elements = []
    if r.get("data"):
        _raw = r["data"]
        if isinstance(_raw, dict):
            _elements = _raw.get("elements", _raw.get("controls", []))
    elif r.get("stdout"):
        import re as _re
        for _l in r["stdout"].split("\n"):
            _m = _re.search(r"(.+?)\s+-\s+(\w+)\s+\((\d+),(\d+)\)\s+(\d+)x(\d+)", _l)
            if _m:
                _elements.append({
                    "name": _m.group(1).strip(),
                    "type": _m.group(2),
                    "rect": {"x": int(_m.group(3)), "y": int(_m.group(4)),
                             "w": int(_m.group(5)), "h": int(_m.group(6))}
                })
    return {"ok": r.get("ok", False), "elements": _elements, "count": len(_elements)}

