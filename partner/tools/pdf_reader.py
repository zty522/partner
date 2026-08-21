"""PDF Reader for Partner (Sprint 7). Extracts text, metadata, images from research papers."""
import os, logging, fitz

logger = logging.getLogger(__name__)


def read_pdf(path: str, max_pages: int = 50, max_chars: int = 50000) -> dict:
    """Read a PDF file and extract text + metadata.
    
    Returns {ok, title, authors, pages, text, size}
    """
    if not os.path.exists(path):
        return {"ok": False, "error": f"File not found: {path}"}
    
    try:
        doc = fitz.open(path)
        metadata = doc.metadata or {}
        
        title = metadata.get("title", "") or os.path.basename(path).replace(".pdf", "")
        author = metadata.get("author", "")
        
        total_pages = len(doc)
        pages = min(total_pages, max_pages)
        texts = []
        for i in range(pages):
            page = doc[i]
            text = page.get_text()
            if text.strip():
                texts.append(text)
            if sum(len(t) for t in texts) > max_chars:
                break
        
        full_text = "\n".join(texts)[:max_chars]
        doc.close()
        
        return {
            "ok": True,
            "path": path,
            "title": title,
            "author": author,
            "pages_total": total_pages,
            "pages_read": pages,
            "text": full_text,
            "size": os.path.getsize(path),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def extract_method_section(path: str, keywords: list = None) -> dict:
    """Extract method-related sections from a research paper.
    
    Searches for sections containing keywords like 'method', 'approach', 'algorithm'.
    """
    if keywords is None:
        keywords = ["method", "approach", "algorithm", "architecture", "model",
                     "training", "pipeline", "implementation"]
    
    result = read_pdf(path, max_chars=100000)
    if not result["ok"]:
        return result
    
    text = result["text"]
    lines = text.split('\n')
    
    # Find sections containing keywords
    method_sections = []
    current_section = ""
    in_section = False
    
    for line in lines:
        stripped = line.strip()
        # Detect section headers (numbered or capitalized)
        is_header = (stripped and (
            stripped[0].isdigit() and '.' in stripped[:5] or
            stripped.isupper() and len(stripped) > 5 and len(stripped) < 80
        ))
        
        if is_header:
            if in_section and current_section:
                method_sections.append(current_section[:2000])
            current_section = stripped + "\n"
            in_section = any(kw.lower() in stripped.lower() for kw in keywords)
        elif in_section:
            current_section += line + "\n"
    
    if in_section and current_section:
        method_sections.append(current_section[:2000])
    
    result["method_sections"] = method_sections[:5]
    result["method_keywords"] = keywords
    return result


def search_pdfs(directory: str, query: str, max_results: int = 10) -> dict:
    """Search PDFs in a directory for a query string."""
    import glob
    
    results = []
    for pdf_path in glob.glob(os.path.join(directory, "**", "*.pdf"), recursive=True):
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(min(len(doc), 20)):
                text = doc[page_num].get_text()
                if query.lower() in text.lower():
                    # Find the matching context
                    idx = text.lower().find(query.lower())
                    start = max(0, idx - 100)
                    end = min(len(text), idx + 200)
                    context = text[start:end].replace('\n', ' ')
                    
                    results.append({
                        "path": pdf_path,
                        "page": page_num + 1,
                        "context": f"...{context}..."[:300],
                        "title": os.path.basename(pdf_path),
                    })
                    break
            doc.close()
            if len(results) >= max_results:
                break
        except Exception:
            pass
    
    return {"ok": True, "query": query, "count": len(results), "results": results}
