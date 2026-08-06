"""
partner/v2/media.py — Media generation module.

Handlers for generating charts, diagrams, annotated screenshots,
visual reports, and memes using matplotlib, seaborn, and PIL.
"""

from __future__ import annotations

import io
import os
import textwrap
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Graceful imports — each block is optional; missing deps raise at call time
# ---------------------------------------------------------------------------

# matplotlib
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    plt = None
    mpatches = None
    FancyBboxPatch = None
    FancyArrowPatch = None

# seaborn
try:
    import seaborn as sns
    HAS_SEABORN = True
except ImportError:
    HAS_SEABORN = False
    sns = None

# PIL
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
    ImageDraw = None
    ImageFont = None

# reportlab (optional — only for PDF reports)
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.lib.units import mm, cm
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# numpy (used by matplotlib/seaborn internally, but check anyway)
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None

# ---------------------------------------------------------------------------
# Chinese font configuration (module-level, runs once on import)
# ---------------------------------------------------------------------------
if HAS_MATPLOTLIB:
    try:
        plt.rcParams["font.sans-serif"] = [
            "WenQuanYi Zen Hei",
            "SimHei",
            "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MEDIA_SUBDIR = "state/media"


def _ensure_media_dir(ctx: Any) -> str:
    """Return and ensure the media save directory exists."""
    base = getattr(ctx, "working_dir", os.getcwd())
    media_dir = os.path.join(base, _MEDIA_SUBDIR)
    os.makedirs(media_dir, exist_ok=True)
    return media_dir


def _resolve_save_path(ctx: Any, save_path: Optional[str], default_name: str) -> str:
    """Return an absolute save path, defaulting to the media directory."""
    if save_path:
        return os.path.abspath(save_path)
    media_dir = _ensure_media_dir(ctx)
    return os.path.join(media_dir, default_name)


# ===================================================================
# 1. atomic_gen_chart — Generate chart using matplotlib/seaborn
# ===================================================================

def atomic_gen_chart(
    ctx: Any,
    params: dict,
) -> dict:
    """Generate a chart from structured data.

    Params
    ------
    data : dict | list
        Chart data.  For bar/line/scatter: a list of dicts with 'x'/'y'
        keys, or a dict mapping labels → values.  For pie: a dict or
        list of (label, value) pairs.  For hist/box: a flat numeric list.
    chart_type : str
        One of: bar, line, scatter, pie, hist, box.
    title : str, optional
        Chart title.
    save_path : str, optional
        Where to save.  Defaults to ``state/media/chart_<type>.png``.
    color_theme : str, optional
        Seaborn palette name (e.g. 'deep', 'muted', 'pastel', 'dark',
        'colorblind').  Ignored if seaborn unavailable.
    """
    if not HAS_MATPLOTLIB:
        return {"ok": False, "error": "matplotlib is not installed"}

    data = params.get("data")
    chart_type = params.get("chart_type", "bar")
    title = params.get("title", "")
    save_path = params.get("save_path")
    color_theme = params.get("color_theme", "deep")

    if data is None:
        return {"ok": False, "error": "'data' parameter is required"}

    # -- apply seaborn style / palette ----------------------------------
    if HAS_SEABORN and color_theme:
        try:
            sns.set_style("whitegrid")
            sns.set_palette(color_theme)
        except Exception:
            pass

    fig, ax = plt.subplots(figsize=(10, 6))

    try:
        chart_type = chart_type.lower()

        if chart_type == "bar":
            if isinstance(data, dict):
                labels = list(data.keys())
                values = list(data.values())
            elif isinstance(data, list) and all(isinstance(d, dict) for d in data):
                labels = [d.get("x", d.get("label", str(i))) for i, d in enumerate(data)]
                values = [d.get("y", d.get("value", 0)) for d in data]
            else:
                labels = list(range(len(data)))
                values = list(data)
            ax.bar(labels, values)
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=45, ha="right")

        elif chart_type == "line":
            if isinstance(data, dict):
                xs = list(data.keys())
                ys = list(data.values())
            elif isinstance(data, list) and all(isinstance(d, dict) for d in data):
                xs = [d.get("x", d.get("label", i)) for i, d in enumerate(data)]
                ys = [d.get("y", d.get("value", 0)) for d in data]
            else:
                xs = list(range(len(data)))
                ys = list(data)
            ax.plot(xs, ys, marker="o", linestyle="-", linewidth=2)

        elif chart_type == "scatter":
            if isinstance(data, dict):
                xs = list(data.keys())
                ys = list(data.values())
            elif isinstance(data, list) and all(isinstance(d, dict) for d in data):
                xs = [d.get("x", d.get("label", i)) for i, d in enumerate(data)]
                ys = [d.get("y", d.get("value", 0)) for d in data]
            else:
                xs = list(range(len(data)))
                ys = list(data)
            ax.scatter(xs, ys, s=60)

        elif chart_type == "pie":
            if isinstance(data, dict):
                labels = list(data.keys())
                sizes = list(data.values())
            elif isinstance(data, list) and all(isinstance(d, dict) for d in data):
                labels = [d.get("x", d.get("label", str(i))) for i, d in enumerate(data)]
                sizes = [d.get("y", d.get("value", 1)) for d in data]
            else:
                labels = [str(i) for i in range(len(data))]
                sizes = list(data)
            ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")

        elif chart_type == "hist":
            if isinstance(data, list):
                values = [v for v in data if isinstance(v, (int, float))]
            elif isinstance(data, dict):
                values = [v for v in data.values() if isinstance(v, (int, float))]
            else:
                values = []
            if not values:
                return {"ok": False, "error": "hist chart requires numeric data"}
            ax.hist(values, bins="auto", edgecolor="black", alpha=0.7)

        elif chart_type == "box":
            if isinstance(data, dict):
                # multiple groups by key
                ax.boxplot(list(data.values()), labels=list(data.keys()))
            elif isinstance(data, list):
                # single group
                numeric = [v for v in data if isinstance(v, (int, float))]
                if not numeric:
                    return {"ok": False, "error": "box chart requires numeric data"}
                ax.boxplot(numeric, vert=True)
            else:
                return {"ok": False, "error": "unsupported data format for box chart"}
            ax.set_xticklabels(
                [str(l) for l in (data.keys() if isinstance(data, dict) else ["data"])],
                rotation=45,
                ha="right",
            )

        else:
            return {"ok": False, "error": f"unknown chart_type: {chart_type}"}

        if title:
            ax.set_title(title, fontsize=14, pad=15)
        ax.set_xlabel(ax.get_xlabel() if ax.get_xlabel() else "")
        ax.set_ylabel(ax.get_ylabel() if ax.get_ylabel() else "")

        # -- save ----------------------------------------------------------
        final_path = _resolve_save_path(ctx, save_path, f"chart_{chart_type}.png")
        fig.tight_layout()
        fig.savefig(final_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return {"ok": True, "path": final_path}

    except Exception as exc:
        plt.close(fig)
        return {"ok": False, "error": str(exc)}


# ===================================================================
# 2. atomic_gen_diagram — Architecture / flow diagram
# ===================================================================

def atomic_gen_diagram(
    ctx: Any,
    params: dict,
) -> dict:
    """Generate an architecture or flow diagram from a text description.

    This uses matplotlib to draw labelled boxes connected by arrows.
    The ``description`` parameter is parsed into a series of nodes and
    edges.  Two ``diagram_type`` modes are supported:

    - ``"architecture"`` : stacked layers (e.g. frontend → backend → db)
    - ``"flow"`` : horizontal flow-chart style

    Params
    ------
    description : str
        Text description of the diagram.  Lines in ``Node -> Node``
        format define edges.  Lines ending with ``::`` define a node
        label.  Lines starting with ``#`` are comments.
    diagram_type : str
        ``"architecture"`` (vertical layers) or ``"flow"`` (horizontal).
    save_path : str, optional
    """
    if not HAS_MATPLOTLIB:
        return {"ok": False, "error": "matplotlib is not installed"}

    description = params.get("description", "")
    diagram_type = params.get("diagram_type", "architecture")
    save_path = params.get("save_path")

    if not description.strip():
        return {"ok": False, "error": "'description' is required"}

    # -- simple text parser ------------------------------------------------
    lines = [l.strip() for l in description.split("\n") if l.strip() and not l.startswith("#")]
    nodes: dict[str, str] = {}       # node_name → display_label
    edges: list[tuple[str, str]] = []  # (from, to)
    node_order: list[str] = []

    for line in lines:
        if "::" in line and "->" not in line:
            # Node definition:  name :: Display Label
            parts = line.split("::", 1)
            name = parts[0].strip()
            label = parts[1].strip()
            if name not in nodes:
                nodes[name] = label
                node_order.append(name)
        elif "->" in line:
            # Edge:  NodeA -> NodeB
            arrow_parts = line.split("->", 1)
            src = arrow_parts[0].strip()
            dst = arrow_parts[1].strip()
            for n in (src, dst):
                if n not in nodes:
                    nodes[n] = n
                    if n not in node_order:
                        node_order.append(n)
            edges.append((src, dst))

    if not nodes:
        # fallback: treat whole description as one box
        nodes["main"] = description[:60]
        node_order = ["main"]

    fig, ax = plt.subplots(figsize=(12, 8))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")

    try:
        if diagram_type == "architecture":
            _draw_architecture(ax, nodes, edges, node_order)
        else:
            _draw_flow(ax, nodes, edges, node_order)

        final_path = _resolve_save_path(ctx, save_path, f"diagram_{diagram_type}.png")
        fig.tight_layout()
        fig.savefig(final_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return {"ok": True, "path": final_path}

    except Exception as exc:
        plt.close(fig)
        return {"ok": False, "error": str(exc)}


def _draw_architecture(
    ax: Any,
    nodes: dict[str, str],
    edges: list[tuple[str, str]],
    node_order: list[str],
) -> None:
    """Stack nodes vertically — top-to-bottom layers."""
    n = len(node_order)
    if n == 0:
        return

    box_w = 6.0
    box_h = 1.2
    gap = 1.5
    start_y = 9.0

    positions: dict[str, tuple[float, float]] = {}

    for i, name in enumerate(node_order):
        cx = 5.0
        cy = start_y - i * (box_h + gap)
        x = cx - box_w / 2
        y = cy - box_h / 2

        rect = FancyBboxPatch(
            (x, y),
            box_w,
            box_h,
            boxstyle="round,pad=0.15",
            edgecolor="#2c3e50",
            facecolor="#ecf0f1",
            linewidth=2,
        )
        ax.add_patch(rect)
        ax.text(
            cx,
            cy,
            nodes[name],
            ha="center",
            va="center",
            fontsize=11,
            fontweight="bold",
        )
        positions[name] = (cx, cy)

    # draw edges
    for src, dst in positions:
        if src not in positions or dst not in positions:
            continue
        (x1, y1) = positions[src]
        (x2, y2) = positions[dst]
        ax.annotate(
            "",
            xy=(x2, y2 + box_h / 2),
            xytext=(x1, y1 - box_h / 2),
            arrowprops=dict(
                arrowstyle="->",
                color="#7f8c8d",
                lw=1.5,
            ),
        )


def _draw_flow(
    ax: Any,
    nodes: dict[str, str],
    edges: list[tuple[str, str]],
    node_order: list[str],
) -> None:
    """Left-to-right flow chart."""
    n = len(node_order)
    if n == 0:
        return

    box_w = 2.5
    box_h = 1.5
    gap = 1.8
    start_x = 1.0
    center_y = 5.0

    positions: dict[str, tuple[float, float]] = {}

    for i, name in enumerate(node_order):
        cx = start_x + i * (box_w + gap)
        cy = center_y
        x = cx - box_w / 2
        y = cy - box_h / 2

        rect = FancyBboxPatch(
            (x, y),
            box_w,
            box_h,
            boxstyle="round,pad=0.1",
            edgecolor="#2980b9",
            facecolor="#d6eaf8",
            linewidth=2,
        )
        ax.add_patch(rect)

        # wrap text if long
        label = nodes[name]
        if len(label) > 18:
            label = "\n".join(textwrap.wrap(label, 18))
        ax.text(
            cx,
            cy,
            label,
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
        )
        positions[name] = (cx, cy)

    # draw edges following connection order from edges list
    for src, dst in edges:
        if src not in positions or dst not in positions:
            continue
        (x1, y1) = positions[src]
        (x2, y2) = positions[dst]
        ax.annotate(
            "",
            xy=(x2 - box_w / 2, y2),
            xytext=(x1 + box_w / 2, y1),
            arrowprops=dict(
                arrowstyle="->",
                color="#2980b9",
                lw=1.5,
            ),
        )


# ===================================================================
# 3. atomic_gen_screenshot_annotated — Annotate an image
# ===================================================================

def atomic_gen_screenshot_annotated(
    ctx: Any,
    params: dict,
) -> dict:
    """Add annotations to an image.

    Params
    ------
    image_path : str
        Path to source image.
    annotations : list[dict]
        Each dict with keys:
        - type : 'arrow' | 'rect' | 'text'
        - x, y : float (top-left corner or arrow start)
        - w, h : float (width/height for rect; arrow direction offset)
        - text : str, optional
        - color : str, optional (default '#FF0000')
    save_path : str, optional
    """
    if not HAS_PIL:
        return {"ok": False, "error": "PIL (Pillow) is not installed"}

    image_path = params.get("image_path")
    annotations = params.get("annotations", [])
    save_path = params.get("save_path")

    if not image_path or not os.path.isfile(image_path):
        return {"ok": False, "error": f"image_path not found: {image_path}"}
    if not annotations:
        return {"ok": False, "error": "annotations list is empty"}

    try:
        img = Image.open(image_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Try to load a font; fall back to default
        font = None
        if ImageFont:
            try:
                font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 20
                )
            except (OSError, IOError):
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
                except (OSError, IOError):
                    pass

        for ann in annotations:
            ann_type = ann.get("type", "text")
            x = ann.get("x", 0)
            y = ann.get("y", 0)
            w = ann.get("w", 30)
            h = ann.get("h", 30)
            text = ann.get("text", "")
            color = ann.get("color", "#FF0000")
            fill_color = color

            if ann_type == "rect":
                draw.rectangle(
                    [x, y, x + w, y + h],
                    outline=fill_color,
                    width=3,
                )
                if text:
                    draw.text(
                        (x + 2, y - 18 if y - 18 > 0 else y + h + 2),
                        text,
                        fill=fill_color,
                        font=font,
                    )

            elif ann_type == "arrow":
                ex = x + w
                ey = y + h
                draw.line([(x, y), (ex, ey)], fill=fill_color, width=3)
                # arrowhead
                arrow_len = 12
                angle = np.arctan2(ey - y, ex - x) if HAS_NUMPY else 0.785
                if HAS_NUMPY:
                    p1 = (
                        ex - arrow_len * np.cos(angle - 0.4),
                        ey - arrow_len * np.sin(angle - 0.4),
                    )
                    p2 = (
                        ex - arrow_len * np.cos(angle + 0.4),
                        ey - arrow_len * np.sin(angle + 0.4),
                    )
                    draw.polygon([(ex, ey), p1, p2], fill=fill_color)
                if text:
                    draw.text(
                        (x + 5, y - 18 if y - 18 > 0 else y + 5),
                        text,
                        fill=fill_color,
                        font=font,
                    )

            elif ann_type == "text":
                draw.text((x, y), text, fill=fill_color, font=font)

            else:
                continue

        img = Image.alpha_composite(img, overlay)
        final_path = _resolve_save_path(ctx, save_path, "annotated_screenshot.png")
        img.save(final_path)
        return {"ok": True, "path": final_path}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ===================================================================
# 4. atomic_gen_visual_report — Generate visual report
# ===================================================================

def atomic_gen_visual_report(
    ctx: Any,
    params: dict,
) -> dict:
    """Generate a visual report with sections each containing text and
    optional charts.

    Params
    ------
    sections : list[dict]
        Each dict:
        - title : str
        - text : str (can include markdown-like formatting)
        - chart_data : optional (passed to atomic_gen_chart)
        - chart_type : optional
    title : str
        Overall report title.
    save_path : str, optional
    format : str
        ``"md"`` (markdown) or ``"pdf"`` (via reportlab).
    """
    sections = params.get("sections", [])
    title = params.get("title", "Report")
    save_path = params.get("save_path")
    fmt = params.get("format", "md").lower()

    if not sections:
        return {"ok": False, "error": "sections list is empty"}

    try:
        if fmt == "pdf":
            return _gen_pdf_report(ctx, title, sections, save_path)
        else:
            return _gen_md_report(ctx, title, sections, save_path)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _gen_md_report(
    ctx: Any,
    title: str,
    sections: list[dict],
    save_path: Optional[str],
) -> dict:
    """Generate a Markdown report, embedding chart images."""
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")

    for i, sec in enumerate(sections):
        sec_title = sec.get("title", f"Section {i + 1}")
        sec_text = sec.get("text", "")
        chart_data = sec.get("chart_data")
        chart_type = sec.get("chart_type", "bar")

        lines.append(f"## {sec_title}")
        lines.append("")
        lines.append(sec_text)
        lines.append("")

        if chart_data:
            chart_result = atomic_gen_chart(
                ctx,
                {
                    "data": chart_data,
                    "chart_type": chart_type or "bar",
                    "title": f"{sec_title} — chart",
                },
            )
            if chart_result.get("ok"):
                # Embed as relative path from the report's perspective
                rel_path = os.path.relpath(
                    chart_result["path"],
                    os.path.dirname(
                        _resolve_save_path(ctx, save_path, "report.md")
                    ),
                )
                lines.append(f"![{sec_title} chart]({rel_path})")
                lines.append("")

    final_path = _resolve_save_path(
        ctx, save_path, f"{title.lower().replace(' ', '_')}.md"
    )
    with open(final_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return {"ok": True, "path": final_path}


def _gen_pdf_report(
    ctx: Any,
    title: str,
    sections: list[dict],
    save_path: Optional[str],
) -> dict:
    """Generate a PDF report using reportlab."""
    if not HAS_REPORTLAB:
        # Fallback: generate MD instead
        return _gen_md_report(ctx, title, sections, save_path)

    final_path = _resolve_save_path(
        ctx, save_path, f"{title.lower().replace(' ', '_')}.pdf"
    )

    doc = SimpleDocTemplate(final_path, pagesize=A4)
    styles = getSampleStyleSheet()
    story: list = []

    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=22,
        spaceAfter=24,
        alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading2"],
        fontSize=16,
        spaceBefore=16,
        spaceAfter=8,
    )
    body_style = styles["Normal"]

    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 12))

    for sec in sections:
        sec_title = sec.get("title", "Section")
        sec_text = sec.get("text", "")
        chart_data = sec.get("chart_data")
        chart_type = sec.get("chart_type")

        story.append(Paragraph(sec_title, section_style))
        # Escape XML entities for reportlab
        safe_text = (
            sec_text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        story.append(Paragraph(safe_text.replace("\n", "<br/>"), body_style))
        story.append(Spacer(1, 8))

        if chart_data:
            chart_result = atomic_gen_chart(
                ctx,
                {
                    "data": chart_data,
                    "chart_type": chart_type or "bar",
                    "title": sec_title,
                },
            )
            if chart_result.get("ok"):
                from reportlab.platypus import Image as RLImage
                img = RLImage(chart_result["path"], width=450, height=250)
                story.append(img)
                story.append(Spacer(1, 12))

    doc.build(story)
    return {"ok": True, "path": final_path}


# ===================================================================
# 5. atomic_gen_meme — Generate simple meme
# ===================================================================

def atomic_gen_meme(
    ctx: Any,
    params: dict,
) -> dict:
    """Generate a simple meme image.

    Params
    ------
    template : str
        One of: ``"impact"``, ``"drake"``, ``"distracted"``, ``"custom"``.
    top_text : str
        Top / first caption.
    bottom_text : str
        Bottom / second caption.
    save_path : str, optional
    color : str, optional
        Background hex colour (default ``"#FFFFFF"``).
    """
    if not HAS_PIL:
        return {"ok": False, "error": "PIL (Pillow) is not installed"}

    template = params.get("template", "impact").lower()
    top_text = params.get("top_text", "")
    bottom_text = params.get("bottom_text", "")
    save_path = params.get("save_path")
    bg_color = params.get("color", "#FFFFFF")

    # Validate template
    valid_templates = {"impact", "drake", "distracted", "custom"}
    if template not in valid_templates:
        return {
            "ok": False,
            "error": f"unknown template '{template}'; choose from {valid_templates}",
        }

    try:
        if template == "drake":
            img = _meme_drake(top_text, bottom_text, bg_color)
        elif template == "distracted":
            img = _meme_distracted(top_text, bottom_text, bg_color)
        elif template in ("impact", "custom"):
            img = _meme_impact(top_text, bottom_text, bg_color, template == "custom")

        final_path = _resolve_save_path(ctx, save_path, f"meme_{template}.png")
        img.save(final_path)
        return {"ok": True, "path": final_path}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _load_font(size: int = 36):
    """Try to load a truetype font; fall back to default."""
    if not ImageFont:
        return None
    for path in (
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return None


def _draw_centered_text(
    draw: Any,
    text: str,
    y: int,
    width: int,
    font: Any,
    fill: str = "black",
    stroke_width: int = 2,
    stroke_fill: str = "white",
) -> None:
    """Draw centered text with an outline stroke for readability."""
    if not text:
        return
    # Approximate text width using bbox
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (width - tw) // 2
    # Draw stroke (outline) by offsetting
    for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
        draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y), text, font=font, fill=fill)


def _meme_impact(
    top_text: str,
    bottom_text: str,
    bg_color: str,
    is_custom: bool = False,
) -> Image.Image:
    """Classic top/bottom text meme on a solid background."""
    width = 600
    height = 600 if not is_custom else 500
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    font = _load_font(40)
    if font is None:
        font = _load_font(32)

    y_center = height // 2
    # draw a subtle horizontal divider for the "impact" style
    if not is_custom:
        draw.line(
            [(50, y_center), (width - 50, y_center)],
            fill="#CCCCCC",
            width=2,
        )

    _draw_centered_text(draw, top_text, 40, width, font)
    _draw_centered_text(draw, bottom_text, height - 80, width, font)

    return img


def _meme_drake(
    top_text: str,
    bottom_text: str,
    bg_color: str,
) -> Image.Image:
    """Drake hotline-bling style: top half reject (top_text),
    bottom half approve (bottom_text)."""
    width = 500
    half = 300
    full_height = half * 2
    img = Image.new("RGB", (width, full_height), bg_color)
    draw = ImageDraw.Draw(img)

    # Draw a subtle dividing line
    draw.line([(0, half), (width, half)], fill="#999999", width=3)

    font = _load_font(28)
    if font is None:
        font = _load_font(24)

    # Top half — reject (top_text)
    _draw_centered_text(draw, top_text, half // 2 - 20, width, font)
    # Bottom half — approve (bottom_text)
    _draw_centered_text(draw, bottom_text, half + half // 2 - 20, width, font)

    return img


def _meme_distracted(
    top_text: str,
    bottom_text: str,
    bg_color: str,
) -> Image.Image:
    """Distracted-boyfriend style: three columns — left (bottom_text)
    as the boyfriend looking right, center (top_text) as the girlfriend
    walking by, right as the 'other' person (text only placeholder)."""
    width = 700
    height = 400
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    font = _load_font(26)
    if font is None:
        font = _load_font(20)

    # Left zone: "boyfriend" — bottom_text
    lx = width * 0.15
    ly = height * 0.7
    bbox_l = draw.textbbox((0, 0), bottom_text, font=font)
    draw.text(
        (lx - (bbox_l[2] - bbox_l[0]) // 2, ly),
        bottom_text,
        font=font,
        fill="black",
    )
    # Left label
    lbl = draw.textbbox((0, 0), "(boyfriend)", font=font)
    draw.text(
        (lx - (lbl[2] - lbl[0]) // 2, ly + 30),
        "(boyfriend)",
        font=font,
        fill="#888888",
    )

    # Center zone: "distraction" — top_text
    cx = width * 0.5
    cy = height * 0.35
    bbox_c = draw.textbbox((0, 0), top_text, font=font)
    draw.text(
        (cx - (bbox_c[2] - bbox_c[0]) // 2, cy),
        top_text,
        font=font,
        fill="black",
    )
    lbl2 = draw.textbbox((0, 0), "(distraction)", font=font)
    draw.text(
        (cx - (lbl2[2] - lbl2[0]) // 2, cy + 30),
        "(distraction)",
        font=font,
        fill="#888888",
    )

    # Right zone: "girlfriend" — top_text reversed role
    rx = width * 0.85
    ry = height * 0.7
    bbox_r = draw.textbbox((0, 0), top_text, font=font)
    draw.text(
        (rx - (bbox_r[2] - bbox_r[0]) // 2, ry),
        top_text,
        font=font,
        fill="black",
    )
    lbl3 = draw.textbbox((0, 0), "(girlfriend)", font=font)
    draw.text(
        (rx - (lbl3[2] - lbl3[0]) // 2, ry + 30),
        "(girlfriend)",
        font=font,
        fill="#888888",
    )

    # Directional arrows to convey the "looking away" joke
    draw.line(
        [(lx + 20, ly - 10), (cx - 20, cy + 10)],
        fill="#FF0000",
        width=2,
    )
    draw.line(
        [(cx + 20, cy + 10), (rx - 20, ry - 10)],
        fill="#00AA00",
        width=2,
    )

    return img
