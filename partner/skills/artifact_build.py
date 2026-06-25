"""Artifact builder — handles PDF generation with strict validation and error reporting.

Only returns ok=True when a valid PDF is successfully generated. Fallback/placeholder
content is rejected, and errors are reported via _error_report.md instead of silently
sending a fake PDF.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime
from typing import Any

from ..utils.text_cleaner import clean_user_facing_text, is_fallback_or_placeholder

logger = logging.getLogger(__name__)


class ArtifactBuilder:
    """Build and validate artifacts (PDFs currently), with strict failure handling."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    async def build_pdf(
        self,
        markdown_content: str,
        output_path: str,
        *,
        title: str = "Partner Report",
        fallbacks_dir: str | None = None,
    ) -> dict[str, Any]:
        """Build a PDF from markdown content.

        Returns:
            On success: {"ok": True, "file_path": str, "format": "pdf"}
            On failure: {"ok": False, "error": str, "error_report_path": str}
        """
        # Step 1: Validate content
        cleaned = clean_user_facing_text(markdown_content)
        if is_fallback_or_placeholder(cleaned):
            msg = f"PDF content is fallback/placeholder (len={len(cleaned)})"
            logger.warning("[ARTIFACT_BUILD] %s for %s", msg, output_path)
            error_path = self._write_error_report(msg, output_path)
            return {
                "ok": False,
                "error": "PDF rendering skipped: content is placeholder/fallback",
                "error_report_path": error_path,
            }

        if not cleaned or len(cleaned) < 100:
            msg = f"markdown content too short ({len(cleaned)} chars, need >= 100)"
            logger.warning("[ARTIFACT_BUILD] %s for %s", msg, output_path)
            error_path = self._write_error_report(msg, output_path)
            return {
                "ok": False,
                "error": msg,
                "error_report_path": error_path,
            }

        # Step 2: Write source markdown
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            md_path = os.path.splitext(output_path)[0] + ".source.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
                if not cleaned.endswith("\n"):
                    f.write("\n")
        except Exception as exc:
            logger.warning("[ARTIFACT_BUILD] failed to write source markdown: %s", exc)
            error_path = self._write_error_report(f"Failed to write source markdown: {exc}", output_path)
            return {"ok": False, "error": str(exc), "error_report_path": error_path}

        # Step 3: Try pandoc
        pdf_path = self._try_pandoc(md_path, output_path)
        if pdf_path:
            logger.info("[ARTIFACT_BUILD] PDF generated via pandoc: %s", pdf_path)
            return {"ok": True, "file_path": pdf_path, "format": "pdf"}

        # Step 4: Try weasyprint
        pdf_path = self._try_weasyprint(cleaned, output_path, title=title)
        if pdf_path:
            logger.info("[ARTIFACT_BUILD] PDF generated via weasyprint: %s", pdf_path)
            return {"ok": True, "file_path": pdf_path, "format": "pdf"}

        # Step 5: Auto-install and retry
        self._auto_install_pdf_tools()
        pdf_path = self._try_pandoc(md_path, output_path)
        if pdf_path:
            logger.info("[ARTIFACT_BUILD] PDF generated after auto-install: %s", pdf_path)
            return {"ok": True, "file_path": pdf_path, "format": "pdf"}

        pdf_path = self._try_weasyprint(cleaned, output_path, title=title)
        if pdf_path:
            logger.info("[ARTIFACT_BUILD] PDF generated via weasyprint after auto-install: %s", pdf_path)
            return {"ok": True, "file_path": pdf_path, "format": "pdf"}

        # Step 6: All rendering methods failed
        msg = "PDF rendering failed: pandoc and weasyprint both unavailable or failed"
        logger.warning("[ARTIFACT_BUILD] %s for %s", msg, output_path)
        error_path = self._write_error_report(msg, output_path)
        return {
            "ok": False,
            "error": msg,
            "error_report_path": error_path,
        }

    # ── Rendering backends ──────────────────────────────────────────────

    def _try_pandoc(self, md_path: str, output_path: str) -> str | None:
        pandoc = shutil.which("pandoc")
        if not pandoc:
            return None
        try:
            result = subprocess.run(
                [pandoc, md_path, "-o", output_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
            if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                return output_path
            logger.warning("[ARTIFACT_BUILD] pandoc stderr: %s", (result.stderr or "")[:500])
        except Exception as exc:
            logger.warning("[ARTIFACT_BUILD] pandoc exception: %s", exc)
        return None

    def _try_weasyprint(self, markdown_text: str, output_path: str, *, title: str) -> str | None:
        try:
            from weasyprint import HTML  # type: ignore
            import markdown as markdown_lib  # type: ignore
        except ImportError:
            return None
        try:
            html = markdown_lib.markdown(
                markdown_text,
                extensions=["tables", "extra"],
            )
            styled = (
                f"<meta charset='utf-8'>"
                f"<style>"
                f"@page {{ size: A4; margin: 2.5cm 2cm 2.5cm 2cm; @bottom-right {{ content: counter(page) ' / ' counter(pages); font-size: 9pt; color: #888; }} }}"
                f"body {{ font-family: 'DejaVu Serif', 'Noto Serif CJK SC', serif; font-size: 11pt; line-height: 1.7; color: #1a1a1a; }}"
                f"h1 {{ font-size: 18pt; color: #1a3a5c; border-bottom: 2px solid #1a3a5c; padding-bottom: 6px; margin-top: 28px; page-break-before: always; }}"
                f"h1:first-of-type {{ page-break-before: avoid; text-align: center; font-size: 22pt; border-bottom: none; margin-top: 120px; }}"
                f"h2 {{ font-size: 14pt; color: #2a5a8c; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 24px; }}"
                f"h3 {{ font-size: 12pt; color: #3a6a9c; margin-top: 18px; }}"
                f"p {{ margin: 8px 0; text-align: justify; }}"
                f"table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 10pt; }}"
                f"th {{ background: #1a3a5c; color: white; padding: 8px 10px; text-align: left; font-weight: bold; }}"
                f"td {{ padding: 6px 10px; border: 1px solid #ddd; }}"
                f"tr:nth-child(even) {{ background: #f5f8fc; }}"
                f"tr:hover {{ background: #e8f0f8; }}"
                f"code {{ font-family: 'DejaVu Sans Mono', monospace; font-size: 9pt; background: #f0f0f0; padding: 2px 5px; border-radius: 3px; }}"
                f"pre {{ background: #f5f5f5; padding: 12px 16px; border-left: 3px solid #1a3a5c; font-size: 9pt; overflow-x: auto; page-break-inside: avoid; }}"
                f"pre code {{ background: none; padding: 0; }}"
                f"blockquote {{ border-left: 3px solid #ccc; margin: 12px 0; padding: 6px 16px; color: #555; font-style: italic; }}"
                f"img {{ max-width: 100%; height: auto; margin: 12px 0; }}"
                f".cover-date {{ text-align: center; font-size: 12pt; color: #666; margin-top: 40px; }}"
                f".cover-subtitle {{ text-align: center; font-size: 14pt; color: #444; margin-top: 20px; }}"
                f"ul, ol {{ margin: 8px 0; padding-left: 24px; }}"
                f"li {{ margin: 4px 0; }}"
                f"</style>"
                f"{html}"
            )
            HTML(string=styled).write_pdf(output_path)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                logger.info("[ARTIFACT_BUILD] weasyprint rendered PDF: %s", output_path)
                return output_path
        except Exception as exc:
            logger.warning("[ARTIFACT_BUILD] weasyprint exception: %s", exc)
        return None

    # ── Error reporting ─────────────────────────────────────────────────

    def _write_error_report(self, error_msg: str, output_path: str) -> str:
        """Write an _error_report.md next to the intended output path."""
        base_dir = os.path.dirname(output_path) or "."
        report_path = os.path.join(base_dir, "_error_report.md")
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(
                    f"# Error Report\n\n"
                    f"- **timestamp**: {datetime.now().isoformat()}\n"
                    f"- **intended_output**: {output_path}\n"
                    f"- **error**: {error_msg}\n\n"
                    f"## Troubleshooting\n\n"
                    f"1. Check that pandoc (with pdflatex) or weasyprint is installed.\n"
                    f"2. Verify the source markdown is valid and >= 100 characters.\n"
                    f"3. Ensure the output directory is writable.\n"
                )
            logger.info("[ARTIFACT_BUILD] error report written to %s", report_path)
        except Exception as exc:
            logger.warning("[ARTIFACT_BUILD] failed to write error report: %s", exc)
        return report_path

    # ── Auto-install ────────────────────────────────────────────────────

    def _auto_install_pdf_tools(self) -> None:
        """Attempt to install missing PDF rendering dependencies."""
        try:
            missing = []
            if not shutil.which("pandoc"):
                missing.append("pandoc")
            if not shutil.which("pdflatex"):
                missing.append("texlive-latex-base")
            if missing:
                logger.info("[AUTO_INSTALL] installing PDF tools: %s", ", ".join(missing))
                subprocess.run(
                    ["sudo", "apt-get", "install", "-y", "-qq"] + missing,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            for pkg in ["weasyprint", "markdown"]:
                try:
                    __import__(pkg.replace("-", "_"))
                except ImportError:
                    subprocess.run(
                        ["pip3", "install", pkg, "-q"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
        except Exception as exc:
            logger.warning("[AUTO_INSTALL] pdf tools install failed: %s", exc)
