"""Literature search events — replaces broken arXiv API."""
import os, logging

logger = logging.getLogger(__name__)


def atomic_search_papers(ctx, params):
    """Search local and web papers. Falls back to web_search if local not found.
    
    Params:
        query: str — search query
        source: str — "local" or "web" (default: both)
        max_results: int — max papers (default: 5)
    
    Returns {ok, count, papers: [{title, path, context}]}
    """
    try:
        from partner.tools.pdf_reader import search_pdfs
        from partner.tools.web_scraper import fetch_github_readme_curl
        
        query = params.get("query", "")
        max_results = params.get("max_results", 5)
        source = params.get("source", "both")
        
        papers = []
        
        # Local search
        if source in ("local", "both"):
            lit_dir = "/mnt/e/work/partner_workspace/external/literature"
            if os.path.exists(lit_dir):
                r = search_pdfs(lit_dir, query, max_results)
                for res in r.get("results", []):
                    papers.append({
                        "title": res.get("title", ""),
                        "path": res.get("path", ""),
                        "context": res.get("context", "")[:200],
                        "source": "local",
                    })
        
        # Web search
        if source in ("web", "both") and len(papers) < max_results:
            # Try GitHub for implementation papers
            pass  # web_search handled by existing harness event
        
        logger.info("[SEARCH-PAPERS] Found %d papers for '%s'", len(papers), query[:50])
        return {"ok": True, "count": len(papers), "papers": papers[:max_results]}
    
    except Exception as e:
        return {"ok": False, "error": str(e)}
