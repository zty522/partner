"""Direct PowerShell operations — screenshot, window list, arXiv search.
Bypasses perception.py. Proven working at OS level. No dependencies beyond stdlib.
"""

import os, subprocess, time, logging, shutil, urllib.request, xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)
CAPTURE_PS1 = r"C:\PartnerTools\capture.ps1"


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


def screenshot(output_dir: str | None = None) -> dict:
    """Capture full-screen screenshot via PowerShell, save to canonical screenshots dir.
    
    Returns {ok, path, size}. Path points to the canonical screenshots directory.
    """
    if output_dir is None:
        output_dir = _get_screenshots_dir()
    os.makedirs(output_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    win_out = f"C:\\temp\\partner_scr_{ts}.png"
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-File", CAPTURE_PS1, "-FullScreen", "-Output", win_out],
                          capture_output=True, text=True, timeout=15)
        wsl_tmp = win_out.replace("C:\\", "/mnt/c/").replace("\\", "/")
        if os.path.exists(wsl_tmp) and os.path.getsize(wsl_tmp) > 100:
            sz = os.path.getsize(wsl_tmp)
            # Move from Windows temp to canonical screenshots dir
            final_path = os.path.join(output_dir, f"winscr_{ts}.png")
            shutil.move(wsl_tmp, final_path)
            logger.info("[direct_ops] Screenshot: %s (%d bytes)", final_path, sz)
            return {"ok": True, "path": final_path, "size": sz}
        return {"ok": False, "error": str(r.stdout)[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_windows(output_dir: str | None = None) -> dict:
    """List all visible windows via PowerShell.
    
    Saves CSV to canonical screenshots dir by default.
    """
    if output_dir is None:
        output_dir = _get_screenshots_dir()
    os.makedirs(output_dir, exist_ok=True)
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-File", CAPTURE_PS1, "-ListWindows"],
                          capture_output=True, text=True, timeout=15)
        windows = []
        for line in r.stdout.strip().split('\n'):
            if line.startswith("WINDOW:"):
                parts = line[7:].split("|")
                if len(parts) >= 3:
                    coords = parts[2].split(",")
                    windows.append({"title": parts[0].strip(), "pid": int(parts[1]),
                                   "x": int(coords[0]), "y": int(coords[1]),
                                   "w": int(coords[2]) if len(coords) > 2 else 0,
                                   "h": int(coords[3]) if len(coords) > 3 else 0})
        ts = time.strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(output_dir, f"windows_{ts}.csv")
        with open(csv_path, "w") as f:
            f.write("title,pid,x,y,w,h\n")
            for w in windows:
                t = w["title"].replace('"', "'")
                f.write(f'"{t}",{w["pid"]},{w["x"]},{w["y"]},{w["w"]},{w["h"]}\n')
        logger.info("[direct_ops] %d windows -> %s", len(windows), csv_path)
        return {"ok": True, "count": len(windows), "windows": windows, "csv_path": csv_path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def capture_window_bg(window_title, output_dir: str | None = None):
    """Capture a specific window by title via PowerShell.
    
    Captures to Windows temp, then moves to canonical screenshots dir.
    """
    if output_dir is None:
        output_dir = _get_screenshots_dir()
    os.makedirs(output_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in window_title)[:30]
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_win = "C:\\temp\\partner_bg_" + safe + "_" + str(os.getpid()) + ".png"
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-File", "C:\\PartnerTools\\capture.ps1",
             "-WindowTitle", window_title, "-Output", out_win],
            capture_output=True, text=True, timeout=15
        )
        if "CAPTURED:" in r.stdout:
            wsl_tmp = out_win.replace("C:\\", "/mnt/c/").replace("\\", "/")
            if os.path.exists(wsl_tmp):
                sz = os.path.getsize(wsl_tmp)
                final_path = os.path.join(output_dir, f"win_bg_{safe}_{ts}.png")
                shutil.move(wsl_tmp, final_path)
                return {"ok": True, "path": final_path, "size": sz}
        return {"ok": False, "error": r.stdout[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def arxiv_search(query: str = "molecular+generation", max_results: int = 5, output_dir: str | None = None) -> dict:
    """Search arXiv and save results as CSV.
    
    Saves to canonical screenshots dir by default.
    """
    if output_dir is None:
        output_dir = _get_screenshots_dir()
    os.makedirs(output_dir, exist_ok=True)
    url = f"http://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Partner/1.0"})
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read().decode("utf-8")
        root = ET.fromstring(data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        papers = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            summary_el = entry.find("atom:summary", ns)
            pub_el = entry.find("atom:published", ns)
            title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""
            summary = summary_el.text.strip().replace("\n", " ") if summary_el is not None and summary_el.text else ""
            published = pub_el.text.strip() if pub_el is not None and pub_el.text else ""
            papers.append({"title": title, "summary": summary[:300], "published": published})
        ts = time.strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(output_dir, f"arxiv_papers_{ts}.csv")
        with open(csv_path, "w") as f:
            f.write("title,summary,published\n")
            for p in papers:
                t = p["title"].replace('"', "'")
                s = p["summary"].replace('"', "'")
                f.write(f'"{t}","{s}","{p["published"]}"\n')
        logger.info("[direct_ops] arXiv: %d papers -> %s", len(papers), csv_path)
        return {"ok": True, "count": len(papers), "papers": papers, "csv_path": csv_path}
    except Exception as e:
        return {"ok": False, "error": str(e)}
