"""用户汇报 PDF 生成事件（atomic_generate_pdf）。

把 harness 已生成的 .md 报告内容或 raw content 转成 PDF，方便用户阅读/归档。
不依赖 LLM，固定格式（标题/正文/代码块样式）。
"""
from __future__ import annotations

import os
import re
import time
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _content_quality(content: str, image_paths: list[str]) -> dict:
    plain = re.sub(r"```.*?```", "", content, flags=re.S)
    plain = re.sub(r"[#>*_`|\-]", "", plain)
    plain_chars = len(re.sub(r"\s+", "", plain))
    sections = len(re.findall(r"^#{2,4}\s+\S", content, flags=re.M))
    evidence_terms = ("证据", "结果", "日志", "文件", "路径", "数据", "方法", "验证", "限制", "风险")
    evidence_hits = sum(1 for term in evidence_terms if term in content)
    return {
        "plain_chars": plain_chars,
        "section_count": sections,
        "evidence_hits": evidence_hits,
        "image_count": len([path for path in image_paths if os.path.isfile(path)]),
    }


def atomic_generate_pdf(ctx, params: dict) -> dict:
    """根据 params 把内容转 PDF。

    支持两种内容来源：
    - content：直接传 markdown 文本
    - source_path：从已写入的 .md 文件读内容
    - workspace：写入位置（默认 task 工作目录）

    返回：{"ok": True, "path": "..."} 或 {"ok": False, "error": ...}
    """
    content = str(params.get("content") or "")
    src_path = str(params.get("source_path") or params.get("input_path") or "")
    image_paths = list(params.get("image_paths") or params.get("images") or [])
    # 自动从 task 工作目录收集最近截图（如 xiaohongshu_*.png）
    if not image_paths:  # 默认自动收集任务目录截图
        params = {**params, "auto_collect_images": True}
    if not image_paths and params.get("auto_collect_images"):
        try:
            base = getattr(ctx, "task_instance", None)
            wd = getattr(base, "working_dir", "") if base is not None else ""
            if not wd:
                wd = getattr(ctx, "working_dir", "") or getattr(ctx, "project_dir", "") or ""
            if wd and os.path.isdir(wd):
                for f in sorted(os.listdir(wd)):
                    if f.lower().endswith((".png", ".jpg", ".jpeg")) and "report" not in f.lower():
                        fp = os.path.join(wd, f)
                        if os.path.getsize(fp) > 50000:  # > 50KB 才是真截图
                            image_paths.append(fp)
        except Exception:
            pass
    if not content and src_path and os.path.exists(src_path):
        try:
            with open(src_path, encoding="utf-8") as source_file:
                content = source_file.read()
        except Exception as e:
            return {"ok": False, "error": f"failed to read source: {e}"}
    if not content:
        return {"ok": False, "error": "no content (provide content or source_path)"}

    quality_profile = str(params.get("quality_profile") or "standard").strip().lower()
    quality = _content_quality(content, image_paths)
    if quality_profile in {"detailed", "detailed_report", "strict"}:
        min_chars = int(params.get("min_content_chars", 1200))
        min_sections = int(params.get("min_sections", 4))
        require_evidence = bool(params.get("require_evidence", True))
        missing = []
        if quality["plain_chars"] < min_chars:
            missing.append(f"正文不足 {min_chars} 字（当前 {quality['plain_chars']}）")
        if quality["section_count"] < min_sections:
            missing.append(f"二级/三级章节不足 {min_sections} 个（当前 {quality['section_count']}）")
        if require_evidence and quality["evidence_hits"] < 2:
            missing.append("缺少可核查的证据、数据、结果或验证说明")
        if missing:
            return {
                "ok": False,
                "status": "content_quality_failed",
                "retryable": False,
                "error": "；".join(missing),
                "quality_profile": quality_profile,
                "quality": quality,
                "missing": missing,
            }

    title = str(params.get("title") or "Partner Task Report")
    out_path = str(params.get("output_path") or params.get("path") or "")
    if not out_path:
        base = getattr(ctx, "task_instance", None)
        wd = getattr(base, "working_dir", "") if base is not None else ""
        if not wd:
            wd = getattr(ctx, "working_dir", "") or getattr(ctx, "project_dir", "") or "/tmp"
        out_path = os.path.join(wd, f"report_{int(time.time())}.pdf")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    # Try reportlab first
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.lib import colors
        # 注册支持 CJK 的字体（reportlab 自带 STSong-Light/HeiseiMin-W3）
        from reportlab.pdfbase import pdfmetrics as _pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont as _UnicodeCIDFont
        _cn_font = "Helvetica"
        for _font_name in ("STSong-Light", "STSongStd-Light", "HeiseiKakuGo-W5"):
            try:
                _pdfmetrics.registerFont(_UnicodeCIDFont(_font_name))
                _cn_font = _font_name
                break
            except Exception:
                continue
        if _cn_font == "Helvetica" and any(ord(ch) > 127 for ch in title + content):
            return {"ok": False, "error": "no registered Unicode CJK font is available for this report"}

        def _escape(value: object) -> str:
            return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        styles = getSampleStyleSheet()
        # 改默认样式的中文字体
        for _sname in ("Normal", "Title", "Heading1", "Heading2", "Heading3", "Heading4", "Italic", "BodyText"):
            try:
                _s = styles[_sname]
                _s.fontName = _cn_font
            except Exception:
                pass
        body_style = ParagraphStyle(
            "Body",
            parent=styles["Normal"],
            fontName=_cn_font,
            fontSize=10,
            leading=14,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#263238"),
            spaceAfter=5,
        )
        styles["Title"].fontName = _cn_font
        styles["Title"].fontSize = 22
        styles["Title"].leading = 29
        styles["Title"].textColor = colors.HexColor("#173F5F")
        styles["Title"].alignment = TA_CENTER
        for _name, _color, _size in (
            ("Heading1", "#173F5F", 17), ("Heading2", "#20639B", 14),
            ("Heading3", "#3CAEA3", 11), ("Heading4", "#555555", 10),
        ):
            styles[_name].fontName = _cn_font
            styles[_name].textColor = colors.HexColor(_color)
            styles[_name].fontSize = _size
            styles[_name].leading = _size + 5
            styles[_name].spaceBefore = 10
            styles[_name].spaceAfter = 6
        doc = SimpleDocTemplate(
            out_path, pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=2*cm, bottomMargin=2*cm,
        )
        subtitle_style = ParagraphStyle(
            "ReportSubtitle", parent=body_style, fontName=_cn_font, fontSize=9,
            leading=12, alignment=TA_CENTER, textColor=colors.HexColor("#607D8B"),
        )
        story = [
            Spacer(1, 0.5 * cm),
            Paragraph(_escape(title), styles["Title"]),
            Spacer(1, 0.18 * cm),
            Paragraph("Partner · 证据驱动执行报告", subtitle_style),
            Spacer(1, 0.25 * cm),
        ]
        from reportlab.platypus import HRFlowable
        story.append(HRFlowable(width="72%", thickness=2, color=colors.HexColor("#3CAEA3"),
                                spaceBefore=3, spaceAfter=14, hAlign="CENTER"))
        # 嵌入图片
        from reportlab.platypus import Image as RLImage
        from reportlab.lib.utils import ImageReader
        for img_path in image_paths:
            if os.path.exists(img_path):
                try:
                    width_px, height_px = ImageReader(img_path).getSize()
                    width = min(16 * cm, float(width_px))
                    height = width * float(height_px) / max(1.0, float(width_px))
                    if height > 20 * cm:
                        height = 20 * cm
                        width = height * float(width_px) / max(1.0, float(height_px))
                    story.append(RLImage(img_path, width=width, height=height))
                    story.append(Paragraph(_escape(f"图：{os.path.basename(img_path)}"), body_style))
                except Exception as e:
                    logger.warning("[GENERATE_PDF] skip image %s: %s", img_path, e)
        # ── 真正的 markdown 解析（# ## ### 标题、表格、列表、代码块、引用、段落） ──
        from reportlab.platypus import Table, TableStyle, ListFlowable, ListItem, KeepTogether
        lines = content.split("\n")
        i = 0
        in_code_block = False
        code_lines = []
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            # 代码块
            if stripped.startswith("```"):
                if in_code_block:
                    # 关闭代码块
                    if code_lines:
                        code_text = "\n".join(code_lines)
                        try:
                            from reportlab.lib.styles import ParagraphStyle as _PS
                            code_style = _PS("Code", parent=body_style, fontName=_cn_font, fontSize=9, leading=11, leftIndent=12, backColor=colors.HexColor("#f5f5f5"), borderPadding=4)
                            code_para = Paragraph(_escape(code_text).replace("\n", "<br/>"), code_style)
                            story.append(KeepTogether([code_para]))
                            story.append(Spacer(1, 0.2*cm))
                        except Exception:
                            pass
                    code_lines = []
                    in_code_block = False
                else:
                    in_code_block = True
                i += 1
                continue
            if in_code_block:
                code_lines.append(line)
                i += 1
                continue
            if not stripped:
                story.append(Spacer(1, 0.15*cm))
                i += 1
                continue
            # 标题
            if stripped.startswith("# "):
                # The document cover already carries the report title.
                if stripped[2:].strip() == title.strip():
                    i += 1
                    continue
                story.append(Paragraph(_escape(stripped[2:].strip()), styles["Heading1"]))
                story.append(Spacer(1, 0.2*cm))
                i += 1
                continue
            if stripped.startswith("## "):
                story.append(Paragraph(_escape(stripped[3:].strip()), styles["Heading2"]))
                story.append(Spacer(1, 0.15*cm))
                i += 1
                continue
            if stripped.startswith("### "):
                story.append(Paragraph(_escape(stripped[4:].strip()), styles["Heading3"]))
                i += 1
                continue
            if stripped.startswith("#### "):
                story.append(Paragraph(_escape(stripped[5:].strip()), styles["Heading4"]))
                i += 1
                continue
            # 引用
            if stripped.startswith("> "):
                from reportlab.lib.styles import ParagraphStyle as _PS
                quote_style = _PS("Quote", parent=body_style, leftIndent=20, fontName=_cn_font, textColor=colors.HexColor("#555555"))
                story.append(Paragraph(_escape(stripped[2:].strip()), quote_style))
                i += 1
                continue
            # 表格（多行 | ... | 连续 + --- 分隔）
            if stripped.startswith("|") and stripped.endswith("|"):
                table_rows = []
                while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                    row_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    table_rows.append(row_cells)
                    i += 1
                # 跳过表头分隔符（---|---|）
                if len(table_rows) >= 2 and all(set(c.replace(":","").replace("-","")) <= set("-: ") for c in table_rows[1]):
                    table_rows.pop(1)
                # 转 reportlab Table
                if table_rows:
                    try:
                        table_body = [[Paragraph(_escape(cell), body_style) for cell in row] for row in table_rows]
                        tbl = Table(table_body, colWidths=None, repeatRows=1)
                        tbl.setStyle(TableStyle([
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("FONTNAME", (0, 0), (-1, -1), _cn_font),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 4),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
                        ]))
                        story.append(tbl)
                        story.append(Spacer(1, 0.2*cm))
                    except Exception:
                        for row in table_rows:
                            story.append(Paragraph(" | ".join(row).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), body_style))
                continue
            # 列表
            if stripped.startswith("- ") or stripped.startswith("* "):
                items = []
                while i < len(lines):
                    s = lines[i].strip()
                    if s.startswith("- ") or s.startswith("* "):
                        items.append(s[2:].strip())
                        i += 1
                    elif s == "":
                        break
                    else:
                        break
                if items:
                    try:
                        list_items = [ListItem(Paragraph(item.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), body_style)) for item in items]
                        story.append(ListFlowable(list_items, bulletType="bullet", leftIndent=14))
                        story.append(Spacer(1, 0.1*cm))
                    except Exception:
                        for it in items:
                            story.append(Paragraph("• " + it, body_style))
                continue
            # 数字列表
            if re.match(r"^\d+\.\s", stripped):
                items = []
                while i < len(lines) and re.match(r"^\s*\d+\.\s", lines[i]):
                    items.append(re.sub(r"^\d+\.\s+", "", lines[i]).strip())
                    i += 1
                if items:
                    try:
                        list_items = [ListItem(Paragraph(item.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), body_style)) for item in items]
                        story.append(ListFlowable(list_items, bulletType="1", leftIndent=14))
                        story.append(Spacer(1, 0.1*cm))
                    except Exception:
                        for idx_, item in enumerate(items):
                            story.append(Paragraph(f"{idx_+1}. {item}", body_style))
                continue
            # 水平线
            if stripped in ("---", "***", "___"):
                story.append(Spacer(1, 0.1*cm))
                story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
                story.append(Spacer(1, 0.1*cm))
                i += 1
                continue
            # 普通段落
            safe = _escape(stripped)
            story.append(Paragraph(safe, body_style))
            i += 1
        # 末尾代码块
        if in_code_block and code_lines:
            try:
                code_text = "\n".join(code_lines)
                code_para = Paragraph(_escape(code_text).replace("\n", "<br/>"), body_style)
                story.append(code_para)
            except Exception:
                pass
        def _page(canvas, document):
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#D7E3EA"))
            canvas.setLineWidth(0.5)
            canvas.line(2 * cm, A4[1] - 1.25 * cm, A4[0] - 2 * cm, A4[1] - 1.25 * cm)
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#78909C"))
            canvas.drawString(2 * cm, A4[1] - 1.05 * cm, "PARTNER · EVIDENCE REPORT")
            canvas.drawRightString(A4[0] - 2 * cm, 1.05 * cm, f"{document.page}")
            canvas.restoreState()

        doc.build(story, onFirstPage=_page, onLaterPages=_page)
        size = os.path.getsize(out_path)
        logger.info("[GENERATE_PDF] wrote %s (%dB)", out_path, size)
        try:
            from partner.evolution.evolution_log import log_evolution
            log_evolution("pdf_generated", detail={"path": out_path, "size": size, "title": title[:50]})
        except Exception:
            pass
        # ── Hook: PDF 路径也触发 self-reflect（与 _atomic_write_artifact 对齐） ──
        # 修复 2026-08-24 cron 实证：5.5h 内 200 条 pdf_generated 但 0 条 report_delivered
        # 根因：_atomic_write_artifact 钩子只在 .md 路径调，PDF 路径（atomic_generate_pdf）
        # 不调 → self-evolution 迭代链沉默。今补 PDF 路径挂钩。
        try:
            from partner.mind.harness import _maybe_trigger_self_reflect_after_write
            _maybe_trigger_self_reflect_after_write(ctx, out_path, content[:1000])
        except Exception:
            pass
        return {
            "ok": True,
            "path": out_path,
            "size": size,
            "files": [out_path],
            "source_path": src_path,
            "quality_profile": quality_profile,
            "quality": quality,
        }
    except ImportError as e:
        return {"ok": False, "error": f"PDF generation requires reportlab: {e}"}
    except Exception as e:
        logger.exception("[GENERATE_PDF] reportlab generation failed")
        return {"ok": False, "error": f"PDF generation failed: {e}"}


def atomic_generate_detailed_pdf(ctx, params: dict) -> dict:
    """Generate a user-facing report only after detailed-content validation."""
    strict_params = {
        **params,
        "quality_profile": "detailed_report",
        "min_content_chars": params.get("min_content_chars", 1200),
        "min_sections": params.get("min_sections", 4),
        "require_evidence": params.get("require_evidence", True),
    }
    return atomic_generate_pdf(ctx, strict_params)
