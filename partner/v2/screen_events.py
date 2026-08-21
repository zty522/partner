"""Background screenshot events for Partner Harness."""
import os, logging

logger = logging.getLogger(__name__)


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


def atomic_capture_bg_window(ctx, params):
    """Capture a specific window in background using PrintWindow API.
    
    Params:
        window_title: str — partial window title to capture (e.g., "Visual Studio Code")
        output_dir: str — output directory (default: canonical screenshots dir)
    
    Returns {ok, path, size}
    """
    try:
        from partner.tools.direct_ops import capture_window_bg
        
        title = params.get("window_title", "")
        out = params.get("output_dir") or _get_screenshots_dir()
        
        r = capture_window_bg(title, out)
        if r["ok"]:
            logger.info("[BG-CAPTURE] %s → %s (%d bytes)", title, r["path"], r.get("size", 0))
        return r
    except Exception as e:
        return {"ok": False, "error": str(e)}


def atomic_list_windows_bg(ctx, params):
    """List all windows in background (PowerShell).
    Returns {ok, count, windows, csv_path}
    """
    try:
        from partner.tools.direct_ops import list_windows
        r = list_windows()
        if r["ok"]:
            logger.info("[BG-LIST] %d windows found", r.get("count", 0))
        return r
    except Exception as e:
        return {"ok": False, "error": str(e)}
