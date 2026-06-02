"""Stage report generation for long-running Partner projects.

The agent writes a compact Markdown report; Partner turns it into user-facing
PPTX/PDF artifacts. This keeps the LLM prompt light while still giving users a
readable "project meeting" output after enough work has accumulated.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from typing import Any


DEFAULT_EVERY_ROUNDS = 24
DEFAULT_MIN_INTERVAL_HOURS = 12


def _now() -> datetime:
    return datetime.now()


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name or "project")
    return name.strip("_") or "project"


def _state_path(workspace: str) -> str:
    state_dir = os.path.join(workspace, "state")
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, "stage_report_state.json")


def _load_state(workspace: str) -> dict[str, Any]:
    try:
        with open(_state_path(workspace), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(workspace: str, state: dict[str, Any]) -> None:
    with open(_state_path(workspace), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _project_report_dir(workspace: str, project: str) -> str:
    from .project_state import get_project_dir

    report_dir = os.path.join(get_project_dir(workspace, project), "reports")
    os.makedirs(report_dir, exist_ok=True)
    return report_dir


def _user_report_dir(workspace: str, project: str) -> str:
    report_dir = os.path.join(workspace, "user", "reports", _safe_name(project))
    os.makedirs(report_dir, exist_ok=True)
    return report_dir


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
        return value if value > 0 else default
    except Exception:
        return default


def maybe_stage_report_objective(workspace: str, project: str, step: int) -> tuple[str, str]:
    """Return a report-writing objective when a project has enough accumulated work."""
    every_rounds = _env_int("PARTNER_STAGE_REPORT_EVERY_ROUNDS", DEFAULT_EVERY_ROUNDS)
    min_interval_hours = _env_int("PARTNER_STAGE_REPORT_MIN_INTERVAL_HOURS", DEFAULT_MIN_INTERVAL_HOURS)
    if step <= 0 or step % every_rounds != 0:
        return "", ""

    state = _load_state(workspace)
    project_state = state.get(project) if isinstance(state.get(project), dict) else {}
    last_at = str(project_state.get("last_generated_at") or "")
    if last_at:
        try:
            last_dt = datetime.fromisoformat(last_at)
            if _now() - last_dt < timedelta(hours=min_interval_hours):
                return "", ""
        except Exception:
            pass

    ts = _now().strftime("%Y%m%d_%H%M")
    path = os.path.join(_project_report_dir(workspace, project), f"stage_report_{ts}.md")
    objective = (
        "做一次阶段汇报，不要继续扩展实验或大范围检索。基于 project_brief、state、exploration_log、"
        "长期记忆和最近产物，写一份面向用户/老师的 Markdown 阶段汇报。"
        "风格参考科研组会汇报：先给诚实结论，再给证据、风险审计、失败边界和下一步。"
        "必须包含这些二级标题：核心结论、已验证进展、关键证据、风险与审计、失败经验与方法边界、下一步计划。"
        "不要堆文件名、目录结构、字节数；不要问用户选方向；如果结果可能有泄露、幻觉或不可复现，要明确写出。"
    )
    return objective, path


def mark_stage_report_generated(workspace: str, project: str, markdown_path: str, outputs: dict[str, str]) -> None:
    state = _load_state(workspace)
    state[project] = {
        "last_generated_at": _now().isoformat(timespec="seconds"),
        "markdown": markdown_path,
        "pptx": outputs.get("pptx", ""),
        "pdf": outputs.get("pdf", ""),
    }
    _save_state(workspace, state)


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _plain(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text or "")
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"[*_#>-]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_markdown(md: str) -> tuple[str, list[tuple[str, list[str]]]]:
    lines = md.splitlines()
    title = "阶段汇报"
    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if current_title or current_lines:
            cleaned = [_plain(x) for x in current_lines if _plain(x)]
            sections.append((current_title or "摘要", cleaned[:8]))
        current_title = ""
        current_lines = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            if title == "阶段汇报":
                title = _plain(line[2:])
            continue
        if line.startswith("## "):
            flush()
            current_title = _plain(line[3:])
            continue
        if line.startswith("### "):
            current_lines.append(_plain(line[4:]))
            continue
        current_lines.append(line)
    flush()
    if not sections:
        sections = [("摘要", [_plain(x) for x in lines if _plain(x)][:8])]
    return title, sections[:8]


def _chunks(items: list[str], size: int = 5) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def _add_textbox(slide, left, top, width, height, text, font_size=20, bold=False, color=None):
    from pptx.util import Pt
    from pptx.dml.color import RGBColor

    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    frame.clear()
    p = frame.paragraphs[0]
    run = p.add_run()
    run.text = text
    run.font.name = "Microsoft YaHei"
    run.font.size = Pt(font_size)
    run.font.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)
    return box


def _generate_pptx(markdown_path: str, project: str, output_path: str) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    title, sections = _parse_markdown(_read_text(markdown_path))
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    navy = (20, 38, 58)
    amber = (199, 119, 35)
    red = (172, 48, 45)
    gray = (90, 95, 102)

    slide = prs.slides.add_slide(blank)
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor(248, 246, 240)
    _add_textbox(slide, Inches(0.7), Inches(0.7), Inches(11.8), Inches(0.8), project, 20, False, amber)
    _add_textbox(slide, Inches(0.7), Inches(1.55), Inches(11.8), Inches(1.2), title, 34, True, navy)
    _add_textbox(slide, Inches(0.75), Inches(3.05), Inches(11.2), Inches(0.7), "阶段性科研汇报：结论、证据、风险审计与下一步", 22, False, gray)
    _add_textbox(slide, Inches(0.75), Inches(5.9), Inches(11), Inches(0.4), datetime.now().strftime("%Y-%m-%d %H:%M"), 14, False, gray)

    for idx, (section_title, lines) in enumerate(sections, start=1):
        for part_idx, group in enumerate(_chunks(lines, 5), start=1):
            slide = prs.slides.add_slide(blank)
            fill = slide.background.fill
            fill.solid()
            fill.fore_color.rgb = RGBColor(255, 255, 255)
            title_text = f"{idx:02d}. {section_title}" if part_idx == 1 else f"{idx:02d}. {section_title}（续）"
            color = red if re.search(r"风险|审计|泄露|失败|边界", section_title) else navy
            _add_textbox(slide, Inches(0.65), Inches(0.45), Inches(11.8), Inches(0.55), title_text, 25, True, color)
            line = slide.shapes.add_shape(1, Inches(0.65), Inches(1.15), Inches(1.25), Inches(0.06))
            line.fill.solid()
            line.fill.fore_color.rgb = RGBColor(*amber)
            line.line.color.rgb = RGBColor(*amber)

            top = 1.55
            for item in group:
                shape = slide.shapes.add_textbox(Inches(0.9), Inches(top), Inches(11.1), Inches(0.7))
                tf = shape.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.text = item[:220]
                p.level = 0
                p.font.name = "Microsoft YaHei"
                p.font.size = Pt(19)
                p.font.color.rgb = RGBColor(36, 42, 50)
                top += 0.88
            _add_textbox(slide, Inches(0.65), Inches(6.95), Inches(11.8), Inches(0.25), "Partner 自动阶段汇报", 10, False, gray)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    prs.save(output_path)


def _generate_pdf_from_markdown(markdown_path: str, project: str, output_path: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        font_name = "STSong-Light"
    except Exception:
        font_name = "Helvetica"

    title, sections = _parse_markdown(_read_text(markdown_path))
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CNTitle", fontName=font_name, fontSize=22, leading=28, textColor=colors.HexColor("#14263a"), spaceAfter=16))
    styles.add(ParagraphStyle(name="CNHeading", fontName=font_name, fontSize=15, leading=20, textColor=colors.HexColor("#ac302d"), spaceBefore=14, spaceAfter=8))
    styles.add(ParagraphStyle(name="CNBody", fontName=font_name, fontSize=10.5, leading=16, textColor=colors.HexColor("#242a32"), leftIndent=8))
    styles.add(ParagraphStyle(name="CNMeta", fontName=font_name, fontSize=9, leading=13, textColor=colors.HexColor("#666666"), spaceAfter=10))
    story = [
        Paragraph(project, styles["CNMeta"]),
        Paragraph(title, styles["CNTitle"]),
        Paragraph(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}", styles["CNMeta"]),
    ]
    for idx, (section_title, lines) in enumerate(sections, start=1):
        story.append(Paragraph(f"{idx:02d}. {section_title}", styles["CNHeading"]))
        for item in lines or ["待补充。"]:
            story.append(Paragraph("• " + item, styles["CNBody"]))
            story.append(Spacer(1, 0.12 * cm))
        if idx in {3, 6}:
            story.append(PageBreak())
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    SimpleDocTemplate(output_path, pagesize=A4, rightMargin=1.5 * cm, leftMargin=1.5 * cm, topMargin=1.3 * cm, bottomMargin=1.3 * cm).build(story)


def _try_convert_pptx_to_pdf(pptx_path: str, pdf_path: str) -> bool:
    libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not libreoffice:
        return False
    outdir = os.path.dirname(pdf_path)
    try:
        result = subprocess.run(
            [libreoffice, "--headless", "--convert-to", "pdf", "--outdir", outdir, pptx_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
            check=False,
        )
        converted = os.path.join(outdir, os.path.splitext(os.path.basename(pptx_path))[0] + ".pdf")
        if result.returncode == 0 and os.path.exists(converted):
            if os.path.abspath(converted) != os.path.abspath(pdf_path):
                shutil.move(converted, pdf_path)
            return True
    except Exception:
        return False
    return False


def publish_stage_report(workspace: str, project: str, markdown_path: str) -> dict[str, str]:
    """Create PPTX/PDF copies for a Markdown stage report and expose them to user/."""
    if not markdown_path or not os.path.exists(markdown_path):
        return {}
    report_dir = _project_report_dir(workspace, project)
    ts = _now().strftime("%Y%m%d_%H%M")
    base = f"{_safe_name(project)}_stage_report_{ts}"
    pptx_path = os.path.join(report_dir, base + ".pptx")
    pdf_path = os.path.join(report_dir, base + ".pdf")

    _generate_pptx(markdown_path, project, pptx_path)
    if not _try_convert_pptx_to_pdf(pptx_path, pdf_path):
        _generate_pdf_from_markdown(markdown_path, project, pdf_path)

    user_dir = _user_report_dir(workspace, project)
    user_md = os.path.join(user_dir, "latest_stage_report.md")
    user_pptx = os.path.join(user_dir, os.path.basename(pptx_path))
    user_pdf = os.path.join(user_dir, os.path.basename(pdf_path))
    shutil.copy2(markdown_path, user_md)
    shutil.copy2(pptx_path, user_pptx)
    shutil.copy2(pdf_path, user_pdf)
    readme_path = os.path.join(user_dir, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(
            f"# {project} 阶段汇报\n\n"
            "- `latest_stage_report.md`：最新文字版汇报\n"
            f"- `{os.path.basename(user_pptx)}`：最新 PPT 汇报\n"
            f"- `{os.path.basename(user_pdf)}`：最新 PDF 汇报\n"
        )
    outputs = {"markdown": user_md, "pptx": user_pptx, "pdf": user_pdf}
    mark_stage_report_generated(workspace, project, markdown_path, outputs)
    return outputs
