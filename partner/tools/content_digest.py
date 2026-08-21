"""Content Digest — turn papers into research hypotheses (Sprint 7)."""
import os, logging

logger = logging.getLogger(__name__)


def digest_paper(pdf_path: str, adapter=None) -> dict:
    """Read a paper, extract the core method, and generate a research hypothesis.
    
    Returns {ok, paper_title, core_method, hypothesis, next_steps}
    """
    try:
        from partner.tools.pdf_reader import read_pdf, extract_method_section
        
        paper = read_pdf(pdf_path, max_pages=10, max_chars=30000)
        if not paper["ok"]:
            return {"ok": False, "error": f"Cannot read paper: {paper.get('error')}"}
        
        methods = extract_method_section(pdf_path)
        method_text = "\n".join(methods.get("method_sections", [])[:3])[:5000]
        
        result = {
            "ok": True,
            "paper_title": paper["title"],
            "paper_pages": paper["pages_total"],
            "text_length": len(paper["text"]),
            "method_sections": len(methods.get("method_sections", [])),
            "method_excerpt": method_text[:1000] if method_text else "",
        }
        
        # If LLM adapter available, generate hypothesis
        if adapter and method_text:
            prompt = f"""You are a research scientist. Read this excerpt from a paper and propose a concrete research experiment.

Paper: {paper['title']}
Method excerpt:
{method_text[:2000]}

Propose:
1. CORE METHOD (one sentence): what is the key innovation?
2. HYPOTHESIS: if we apply/reimplement this method in our context, what would we expect?
3. EXPERIMENT (3 steps): how to test the hypothesis
4. POTENTIAL IMPROVEMENT: one way to improve upon this method

Be specific and actionable. No vague suggestions."""
            try:
                response = adapter.chat(prompt, purpose="content_digest")
                result["llm_analysis"] = response[:2000]
                logger.info("[digest] Analyzed %s", paper['title'][:60])
            except Exception as e:
                result["llm_analysis"] = f"LLM unavailable: {e}"
        else:
            result["llm_analysis"] = "No adapter available for hypothesis generation"
        
        return result
    
    except Exception as e:
        return {"ok": False, "error": str(e)}


def digest_directory(directory: str, adapter=None, max_papers: int = 5) -> dict:
    """Digest all PDFs in a directory and rank by relevance."""
    import glob
    
    papers = []
    for pdf_path in glob.glob(os.path.join(directory, "**", "*.pdf"), recursive=True):
        try:
            result = digest_paper(pdf_path, adapter)
            if result["ok"]:
                papers.append(result)
            if len(papers) >= max_papers:
                break
        except Exception:
            pass
    
    # Rank by method_sections count (more sections = more method-rich)
    papers.sort(key=lambda p: p.get("method_sections", 0), reverse=True)
    
    return {"ok": True, "papers_digested": len(papers), "papers": papers}
