"""
perception.py — Handler functions for the Partner harness.

Each handler has signature: atomic_XXX(ctx, params) -> dict.
All imports are try/except guarded for resilience.
"""

import logging
import os
import sys
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guarded imports
# ---------------------------------------------------------------------------

try:
    import pyautogui
except Exception:
    try:
        import os as _os
        _os.environ.setdefault('DISPLAY', ':99')
        import pyautogui
    except Exception:
        pyautogui = None

try:
    import cv2
except Exception:
    cv2 = None

try:
    import mss
except Exception:
    mss = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import psutil
except ImportError:
    psutil = None

try:
    import GPUtil
except ImportError:
    GPUtil = None

try:
    import pygetwindow as gw
except Exception:
    gw = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _screenshot_dir(ctx):
    """Return the screenshot storage directory under workspace, creating it if needed."""
    # Try to resolve workspace via ctx or PARTNER_DATA_DIR
    _base = os.environ.get("PARTNER_DATA_DIR", "")
    if not _base:
        _ws = getattr(ctx, "working_dir", getattr(ctx, "project_dir", ""))
        if _ws:
            _base = os.path.join(_ws, "partner_data")
    if not _base:
        _base = os.path.join(os.getcwd(), "partner_data")
    snap_dir = os.path.join(_base, "screenshots")
    os.makedirs(snap_dir, exist_ok=True)
    return snap_dir


def _latest_screenshot_path(ctx):
    """Return the path of the most recent screenshot in the screenshots dir."""
    snap_dir = _screenshot_dir(ctx)
    try:
        files = sorted(
            [f for f in os.listdir(snap_dir) if f.endswith((".png", ".jpg", ".jpeg"))],
            key=lambda f: os.path.getmtime(os.path.join(snap_dir, f)),
            reverse=True,
        )
        if files:
            return os.path.join(snap_dir, files[0])
    except Exception:
        pass
    return None


def _ensure_image(image_path, ctx):
    """Resolve image_path, falling back to latest screenshot."""
    if image_path:
        return image_path
    latest = _latest_screenshot_path(ctx)
    if latest is None:
        raise FileNotFoundError("No image_path provided and no screenshots available.")
    return latest


# ---------------------------------------------------------------------------
# 1. atomic_screen_capture
# ---------------------------------------------------------------------------

def atomic_screen_capture(ctx, params):
    """Capture a screenshot (optionally a region) and save to disk.

    Params:
        region (list[int] | None): [x, y, w, h] bounding box.
        save_path (str | None):    Explicit file path.  Defaults to
                                   workspace/state/screenshots/{timestamp}.png

    Returns:
        dict: {ok: bool, path: str, size: {w: int, h: int}}
    """
    region = params.get("region")
    save_path = params.get("save_path")

    # ── Method 1: pyautogui (requires X11) ──
    if pyautogui is not None:
        try:
            bbox = None
            if region and len(region) == 4:
                bbox = tuple(region)
            img = pyautogui.screenshot(region=bbox)
            if save_path:
                out = save_path
            else:
                snap_dir = _screenshot_dir(ctx)
                ts = time.strftime("%Y%m%d_%H%M%S")
                out = os.path.join(snap_dir, f"screenshot_{ts}.png")
            img.save(out)
            return {
                "ok": True,
                "path": out,
                "size": {"w": img.width, "h": img.height},
            }
        except Exception as e:
            logger.warning("[screen_capture] pyautogui failed: %s, falling back to mss", e)

    # ── Method 2: mss (requires X11, works in Xvfb / real displays) ──
    if mss is not None:
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # primary monitor
                if region and len(region) == 4:
                    monitor = {"left": region[0], "top": region[1],
                               "width": region[2], "height": region[3]}
                img = sct.grab(monitor)
                from PIL import Image as _PIL
                pil_img = _PIL.frombytes("RGB", img.size, img.rgb)
                if save_path:
                    out = save_path
                else:
                    snap_dir = _screenshot_dir(ctx)
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    out = os.path.join(snap_dir, f"screenshot_{ts}.png")
                pil_img.save(out)
                return {
                    "ok": True,
                    "path": out,
                    "size": {"w": pil_img.width, "h": pil_img.height},
                }
        except Exception as e:
            logger.warning("[screen_capture] mss also failed: %s, generating synthetic screenshot", e)

    # ── Method 3: Windows PowerShell screenshot (from WSL to real Windows display) ──
    if sys.platform == "linux" and os.path.isdir("/mnt/c"):
        try:
            import subprocess as _sp
            _win_path = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            if not os.path.exists(_win_path):
                _win_path = "/mnt/c/Windows/System32/powershell.exe"
            if os.path.exists(_win_path):
                # Use workspace screenshots dir (not /mnt/c/temp/)
                _snap_dir = _screenshot_dir(ctx)
                _ts = time.strftime("%Y%m%d_%H%M%S")
                _out_wsl = os.path.join(_snap_dir, f"winscr_{_ts}.png")
                _out_win = "C:\\temp\\partner_scr_tmp.png"  # temp staging, will be moved
                _tmp_wsl = "/mnt/c/temp/partner_scr_tmp.png"
                os.makedirs("/mnt/c/temp", exist_ok=True)

                # Build PowerShell script for screenshot
                _ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($screen.X, $screen.Y, 0, 0, $screen.Size)
$bitmap.Save('{_out_win}', [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
Write-Output "$($screen.Width)x$($screen.Height)"
"""
                _r = _sp.run([_win_path, "-NoProfile", "-Command", _ps_script],
                             capture_output=True, text=True, timeout=15)
                if _r.returncode == 0 and os.path.exists(_tmp_wsl):
                    _sz = os.path.getsize(_tmp_wsl)
                    if _sz > 0:
                        import shutil as _shutil
                        _shutil.move(_tmp_wsl, _out_wsl)
                        _dims = _r.stdout.strip().split("x")
                        _w = int(_dims[0]) if len(_dims) >= 1 else 0
                        _h = int(_dims[1]) if len(_dims) >= 2 else 0
                        logger.info("[screen_capture] Windows screenshot: %s (%dx%d, %d bytes)",
                                    _out_wsl, _w, _h, _sz)
                        return {"ok": True, "path": _out_wsl,
                                "size": {"w": _w, "h": _h},
                                "note": "Windows native screenshot via PowerShell"}
        except Exception as e:
            logger.warning("[screen_capture] Windows PowerShell screenshot failed: %s", e)

    return {"ok": False, "path": "", "size": {"w": 0, "h": 0},
            "error": "no screenshot backend available"}


# ---------------------------------------------------------------------------
# 2. atomic_screen_ocr
# ---------------------------------------------------------------------------

def atomic_screen_ocr(ctx, params):
    """OCR a given image (or the latest screenshot).

    Params:
        image_path (str | None): Path to the image.  Defaults to latest screenshot.

    Returns:
        dict: {ok: bool, text: str, blocks: [{bbox, text}]}
    """
    path = None
    try:
        path = _ensure_image(params.get("image_path"), ctx)
    except FileNotFoundError:
        pass

    # ── Method 1: pytesseract (fastest, needs tesseract binary) ──
    if pytesseract is not None and Image is not None and path:
        try:
            img = Image.open(path)
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            text = data.get("text", [])
            left = data.get("left", [])
            top = data.get("top", [])
            width = data.get("width", [])
            height = data.get("height", [])

            blocks = []
            for i in range(len(text)):
                t = text[i].strip()
                if not t:
                    continue
                blocks.append({
                    "bbox": {"x": left[i], "y": top[i],
                             "w": width[i], "h": height[i]},
                    "text": t,
                })

            full_text = " ".join(b["text"] for b in blocks)
            return {"ok": True, "text": full_text, "blocks": blocks,
                    "note": "pytesseract"}
        except Exception as e:
            logger.warning("[screen_ocr] pytesseract failed: %s, trying Windows OCR", e)

    # ── Method 2: Windows OCR API via PowerShell (built-in Win10+) ──
    if sys.platform == "linux" and os.path.isdir("/mnt/c") and path:
        try:
            import subprocess as _sp, json as _json
            # Copy image to Windows temp for processing
            _win_img = "C:\\temp\\partner_ocr_input.png"
            _wsl_img = "/mnt/c/temp/partner_ocr_input.png"
            os.makedirs("/mnt/c/temp", exist_ok=True)
            import shutil as _su
            _su.copy2(path, _wsl_img)

            _ps_script = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$imgPath = '{_win_img}'
$stream = [System.IO.File]::OpenRead($imgPath)
$decoder = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream).GetResults()
$bitmap = $decoder.GetSoftwareBitmapAsync().GetResults()
$ocr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = $ocr.RecognizeAsync($bitmap).GetResults()
$stream.Dispose()
$lines = @()
if ($result -and $result.Lines) {{
    foreach ($line in $result.Lines) {{
        $lines += @{{"text" = $line.Text; "x" = [int]$line.BoundingRect.X; "y" = [int]$line.BoundingRect.Y; "w" = [int]$line.BoundingRect.Width; "h" = [int]$line.BoundingRect.Height}}
    }}
}}
$fullText = ($lines | ForEach-Object {{ $_["text"] }}) -join " "
Write-Output ($fullText + "|||" + (ConvertTo-Json @{{"text" = $fullText; "lines" = $lines}} -Compress))
"""
            _win_path = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            if not os.path.exists(_win_path):
                _win_path = "/mnt/c/Windows/System32/powershell.exe"
            if os.path.exists(_win_path):
                _r = _sp.run([_win_path, "-NoProfile", "-Command", _ps_script],
                             capture_output=True, text=True, timeout=30)
                if _r.returncode == 0 and _r.stdout.strip():
                    _parts = _r.stdout.strip().split("|||", 1)
                    _text = _parts[0].strip()
                    _blocks = []
                    if len(_parts) > 1:
                        try:
                            _data = _json.loads(_parts[1])
                            for _l in _data.get("lines", []):
                                _blocks.append({
                                    "bbox": {"x": _l.get("x", 0), "y": _l.get("y", 0),
                                             "w": _l.get("w", 0), "h": _l.get("h", 0)},
                                    "text": _l.get("text", ""),
                                })
                        except Exception:
                            pass
                    if _text:
                        logger.info("[screen_ocr] Windows OCR: %d chars, %d blocks",
                                    len(_text), len(_blocks))
                        return {"ok": True, "text": _text, "blocks": _blocks,
                                "note": "Windows OCR API"}
            # Cleanup temp
            try:
                os.remove(_wsl_img)
            except Exception:
                pass
        except Exception as e:
            logger.warning("[screen_ocr] Windows OCR also failed: %s", e)

    return {"ok": False, "text": "", "blocks": [],
            "error": "no OCR backend available (install tesseract or use Windows OCR)"}


# ---------------------------------------------------------------------------
# 3. atomic_screen_detect_ui
# ---------------------------------------------------------------------------

def atomic_screen_detect_ui(ctx, params):
    """Detect UI elements via OpenCV contours + OCR bounding boxes.

    Params:
        image_path (str): Path to the image.
        target (str):     Optional target string to filter detected elements.

    Returns:
        dict: {ok: bool, elements: [{type, bbox, text}]}
    """
    if cv2 is None or pytesseract is None:
        return {"ok": False, "elements": []}

    try:
        path = _ensure_image(params.get("image_path"), ctx)
        target = params.get("target", "")

        img_cv = cv2.imread(path)
        if img_cv is None:
            return {"ok": False, "elements": [], "error": f"Could not read image: {path}"}

        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        elements = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # filter very small artefacts
            if w < 10 or h < 10:
                continue

            roi = img_cv[y : y + h, x : x + w]
            text = pytesseract.image_to_string(roi).strip()

            el = {
                "type": "contour",
                "bbox": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)},
                "text": text,
            }
            elements.append(el)

        # Optional filtering by target text
        if target:
            elements = [e for e in elements if target.lower() in e["text"].lower()]

        return {"ok": True, "elements": elements}
    except Exception as exc:
        logger.exception("screen_detect_ui failed")
        return {"ok": False, "elements": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# 4. atomic_screen_find
# ---------------------------------------------------------------------------

def atomic_screen_find(ctx, params):
    """Locate a target (image or text) on screen.

    Params:
        target     (str):  Image path or text string to locate.
        confidence (float): Confidence threshold 0–1 (default 0.8).

    Returns:
        dict: {ok: bool, found: bool, position: {x, y, w, h}}
    """
    target = params.get("target", "")
    confidence = params.get("confidence", 0.8)

    if pyautogui is None or Image is None:
        return {"ok": False, "found": False, "position": {"x": 0, "y": 0, "w": 0, "h": 0}}

    try:
        result = pyautogui.locateOnScreen(target, confidence=confidence)
        if result is not None:
            x, y, w, h = result
            return {
                "ok": True,
                "found": True,
                "position": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)},
            }
        else:
            return {
                "ok": True,
                "found": False,
                "position": {"x": 0, "y": 0, "w": 0, "h": 0},
            }
    except Exception as exc:
        logger.exception("screen_find failed")
        return {
            "ok": False,
            "found": False,
            "position": {"x": 0, "y": 0, "w": 0, "h": 0},
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# 5. atomic_screen_watch
# ---------------------------------------------------------------------------

def atomic_screen_watch(ctx, params):
    """Monitor a screen region for visual changes.

    Params:
        region          (list[int]): [x, y, w, h] area to watch.
        timeout         (int):       Max seconds to watch (default 30).
        poll_interval   (float):     Seconds between polls (default 0.5).
        change_threshold (float):    MSE threshold to consider a change (default 10.0).

    Returns:
        dict: {ok: bool, changed: bool, details: str}
    """
    if pyautogui is None or cv2 is None or np is None:
        return {"ok": False, "changed": False, "details": "Missing dependencies (pyautogui, cv2, numpy)."}

    region = params.get("region")
    timeout = params.get("timeout", 30)
    poll_interval = params.get("poll_interval", 0.5)
    change_threshold = params.get("change_threshold", 10.0)

    if not region or len(region) != 4:
        return {"ok": False, "changed": False, "details": "region=[x,y,w,h] is required."}

    x, y, w, h = region
    bbox = (x, y, w, h)

    try:
        # Grab first frame
        prev_img = pyautogui.screenshot(region=bbox)
        prev_cv = cv2.cvtColor(np.array(prev_img), cv2.COLOR_RGB2GRAY)
        prev_cv = prev_cv.astype(np.float32)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)

            curr_img = pyautogui.screenshot(region=bbox)
            curr_cv = cv2.cvtColor(np.array(curr_img), cv2.COLOR_RGB2GRAY)
            curr_cv = curr_cv.astype(np.float32)

            mse = np.mean((prev_cv - curr_cv) ** 2)

            if mse > change_threshold:
                return {
                    "ok": True,
                    "changed": True,
                    "details": f"Change detected at MSE={mse:.2f} (threshold={change_threshold})",
                }

            prev_cv = curr_cv

        return {
            "ok": True,
            "changed": False,
            "details": f"No change detected within {timeout}s (MSE threshold={change_threshold})",
        }
    except Exception as exc:
        logger.exception("screen_watch failed")
        return {"ok": False, "changed": False, "details": str(exc)}


# ---------------------------------------------------------------------------
# 6. atomic_screen_analyze
# ---------------------------------------------------------------------------

def atomic_screen_analyze(ctx, params):
    """Placeholder for multimodal screen analysis.

    Params:
        image_path (str): Path to the image.
        question   (str): Natural-language question about the image.

    Returns:
        dict: {ok: bool, analysis: str, note: str}
    """
    _ = ctx  # placeholder — ctx reserved for future use
    image_path = params.get("image_path", "")
    question = params.get("question", "")

    # This is a placeholder; a real implementation would use a VLM.
    return {
        "ok": True,
        "analysis": "",
        "note": f"Placeholder — multimodal analysis not yet wired. "
        f"image_path={image_path!r}, question={question!r}",
    }


# ---------------------------------------------------------------------------
# 7. atomic_hardware_info
# ---------------------------------------------------------------------------

def atomic_hardware_info(ctx, params):
    """Query CPU / GPU / memory / disk info.

    Params:
        category (str): One of 'all', 'cpu', 'gpu', 'memory', 'disk' (default 'all').

    Returns:
        dict: {ok: bool, info: dict}
    """
    _ = ctx
    category = (params.get("category") or "all").lower()
    info = {}

    try:
        if category in ("all", "cpu") and psutil is not None:
            info["cpu"] = {
                "physical_cores": psutil.cpu_count(logical=False),
                "logical_cores": psutil.cpu_count(logical=True),
                "percent": psutil.cpu_percent(interval=0.1),
                "frequency_mhz": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None,
            }

        if category in ("all", "memory") and psutil is not None:
            mem = psutil.virtual_memory()
            info["memory"] = {
                "total_bytes": mem.total,
                "available_bytes": mem.available,
                "percent": mem.percent,
                "used_bytes": mem.used,
                "free_bytes": mem.free,
            }

        if category in ("all", "disk") and psutil is not None:
            disk = psutil.disk_usage("/")
            info["disk"] = {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
                "percent": disk.percent,
            }

        if category in ("all", "gpu") and GPUtil is not None:
            try:
                gpus = GPUtil.getGPUs()
                info["gpu"] = [
                    {
                        "name": g.name,
                        "driver": g.driver,
                        "load_percent": g.load * 100,
                        "memory_total_mb": g.memoryTotal,
                        "memory_used_mb": g.memoryUsed,
                        "memory_free_mb": g.memoryFree,
                        "temperature_c": g.temperature,
                    }
                    for g in gpus
                ]
            except Exception as gpu_err:
                info["gpu"] = {"error": str(gpu_err)}

        return {"ok": True, "info": info}
    except Exception as exc:
        logger.exception("hardware_info failed")
        return {"ok": False, "info": {}, "error": str(exc)}


# ---------------------------------------------------------------------------
# 8. atomic_app_list
# ---------------------------------------------------------------------------

def atomic_app_list(ctx, params):
    """List running application windows.

    Params:
        filter (str | None): Optional substring to filter window titles.

    Returns:
        dict: {ok: bool, windows: [{title, left, top, width, height, is_active}]}
    """
    _ = ctx
    if gw is None:
        return {"ok": False, "windows": []}

    try:
        filt = params.get("filter", "")
        all_windows = gw.getWindowsWithTitle("")  # all windows

        windows = []
        for w in all_windows:
            title = w.title
            if filt and filt.lower() not in title.lower():
                continue
            windows.append(
                {
                    "title": title,
                    "left": w.left,
                    "top": w.top,
                    "width": w.width,
                    "height": w.height,
                    "is_active": w.isActive,
                }
            )

        return {"ok": True, "windows": windows}
    except Exception as exc:
        logger.exception("app_list failed")
        return {"ok": False, "windows": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# 9. atomic_app_focus
# ---------------------------------------------------------------------------

def atomic_app_focus(ctx, params):
    """Get or switch focus to a window matching the target title.

    Params:
        target (str): Window title substring to activate.

    Returns:
        dict: {ok: bool, window: {title, geometry}}
    """
    _ = ctx
    if gw is None:
        return {"ok": False, "window": {"title": "", "geometry": {}}}

    try:
        target = params.get("target", "")
        if not target:
            # Return the currently active window
            active = gw.getActiveWindow()
            if active is None:
                return {"ok": False, "window": {"title": "", "geometry": {}}}
            return {
                "ok": True,
                "window": {
                    "title": active.title,
                    "geometry": {
                        "left": active.left,
                        "top": active.top,
                        "width": active.width,
                        "height": active.height,
                    },
                },
            }

        matches = gw.getWindowsWithTitle(target)
        if not matches:
            return {
                "ok": False,
                "window": {"title": "", "geometry": {}},
                "error": f"No window matching {target!r}",
            }

        # Activate the first match
        win = matches[0]
        win.activate()

        return {
            "ok": True,
            "window": {
                "title": win.title,
                "geometry": {
                    "left": win.left,
                    "top": win.top,
                    "width": win.width,
                    "height": win.height,
                },
            },
        }
    except Exception as exc:
        logger.exception("app_focus failed")
        return {
            "ok": False,
            "window": {"title": "", "geometry": {}},
            "error": str(exc),
        }
