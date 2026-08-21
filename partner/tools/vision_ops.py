"""Vision + OCR for Partner (Sprint 7). Analyze screenshots, extract text."""
import os, subprocess, logging

logger = logging.getLogger(__name__)

def ocr_image(image_path: str, language: str = "eng+chi_sim") -> dict:
    """Extract text from image using tesseract OCR."""
    if not os.path.exists(image_path):
        return {"ok": False, "error": f"File not found: {image_path}"}
    try:
        r = subprocess.run(
            ["tesseract", image_path, "stdout", "-l", language, "--psm", "6"],
            capture_output=True, text=True, timeout=30
        )
        text = r.stdout.strip()
        return {"ok": True, "path": image_path, "text": text, "text_length": len(text)}
    except FileNotFoundError:
        return {"ok": False, "error": "tesseract not installed. Run: sudo apt install tesseract-ocr tesseract-ocr-chi-sim"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def analyze_screenshot(image_path: str, questions: list = None) -> dict:
    """Analyze screenshot contents: what windows are visible, what's happening.
    
    Uses basic image analysis (size, dimensions) and OCR.
    Falls back to structural analysis if OCR unavailable.
    """
    if not os.path.exists(image_path):
        return {"ok": False, "error": "File not found"}
    
    result = {
        "ok": True,
        "path": image_path,
        "size": os.path.getsize(image_path),
    }
    
    # Try OCR
    ocr = ocr_image(image_path)
    if ocr["ok"] and ocr["text"]:
        result["ocr_text"] = ocr["text"][:2000]
        result["has_text"] = len(ocr["text"]) > 50
    
    # Answer specific questions about the screenshot
    if questions:
        answers = {}
        if ocr["ok"]:
            text_lower = ocr["text"].lower()
            for q in questions:
                q_lower = q.lower()
                if any(kw in text_lower for kw in q_lower.split()[:3]):
                    # Find context around matching keywords
                    for kw in q_lower.split()[:3]:
                        idx = text_lower.find(kw)
                        if idx >= 0:
                            start = max(0, idx - 50)
                            end = min(len(ocr["text"]), idx + 100)
                            answers[q] = ocr["text"][start:end]
                            break
                else:
                    answers[q] = "not found in screenshot text"
        result["answers"] = answers
    
    return result


def compare_window_lists(before: list, after: list) -> dict:
    """Compare two window lists and report changes."""
    before_titles = {w.get("title", "") for w in before}
    after_titles = {w.get("title", "") for w in after}
    
    new_windows = after_titles - before_titles
    closed_windows = before_titles - after_titles
    still_open = before_titles & after_titles
    
    return {
        "ok": True,
        "before_count": len(before),
        "after_count": len(after),
        "new": sorted(new_windows),
        "closed": sorted(closed_windows),
        "still_open": sorted(still_open),
        "changed": len(new_windows) > 0 or len(closed_windows) > 0,
    }
