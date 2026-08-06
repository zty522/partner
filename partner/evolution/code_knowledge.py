"""Code Knowledge — frontend code analysis and pattern comparison for Partner's self-evolution system.

Part of the 5-step self-evolution cycle. Analyzes external and internal frontend code
to extract UI patterns, compare against Partner's GUI, and generate concrete improvement
plans with real file paths.

Sources supported:
  - "Hermes" → /mnt/e/work/hermes-agent/ (TypeScript React-Ink TUI)
  - "OpenClaw" → /home/os/.openclaw/ (compiled JS agent framework)

Output:
  Each comparison produces a structured diff list. generate_frontend_improvements()
  converts each diff into a full improvement plan consumable by SelfEvolveEngine
  and plan_formation.py, with correct target_module paths under
  shells/frontend/desktop_gui/modern/.

Usage:
    from partner.evolution.code_knowledge import CodeKnowledge

    ck = CodeKnowledge()
    patterns = ck.analyze_frontend_structure("/mnt/e/work/hermes-agent/")
    diffs = ck.compare_with_partner(patterns)
    plans = ck.generate_frontend_improvements(diffs)
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

PARTNER_GUI_ROOT = Path(
    "/mnt/e/work/partner/shells/frontend/desktop_gui/modern"
)

KNOWN_SOURCES: dict[str, str] = {
    "Hermes": "/mnt/e/work/hermes-agent/",
    "OpenClaw": "/home/os/.openclaw/",
}

# UI component file patterns by framework
UI_FILE_PATTERNS = {
    "widget": re.compile(
        r"(widget|component|bubble|button|card|input|label|box|panel|bar|item|dialog|frame)"
    ),
    "style": re.compile(r"(theme|style|stylesheet|color|palette|css|qss|sass|less)"),
    "layout": re.compile(r"(layout|container|grid|flex|sidebar|nav|pane|split)"),
    "page": re.compile(
        r"(page|screen|view|dashboard|settings|chat|home|agent|instance|login|config)"
    ),
}

# ── Data Types ─────────────────────────────────────────────────────────────────


@dataclass
class FileCategory:
    """Categorised file within a frontend codebase."""

    path: str
    file_type: str  # "widget" | "style" | "layout" | "page" | "other"
    lines: int
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    summary: str = ""
    content: str = ""  # cached file content to avoid re-reads


@dataclass
class ComponentInfo:
    """A UI component extracted from source analysis."""

    name: str
    file_path: str
    framework: str  # "PySide6" | "React-Ink" | "unknown"
    widget_type: str  # "container" | "button" | "input" | "display" | "card" | "layout"
    has_hover: bool = False
    has_click: bool = False
    has_keyboard: bool = False
    has_expand: bool = False
    has_scroll: bool = False
    has_animation: bool = False
    children: list[str] = field(default_factory=list)
    line_count: int = 0


@dataclass
class UIPattern:
    """Structured UI patterns extracted from a frontend codebase."""

    source: str
    framework: str
    component_hierarchy: list[ComponentInfo] = field(default_factory=list)
    layout_approach: str = ""
    styling_mechanism: str = ""
    interaction_patterns: list[str] = field(default_factory=list)
    component_count: int = 0
    file_count: int = 0


@dataclass
class DiffEntry:
    """A single concrete difference between an external pattern and Partner's GUI."""

    external_component: str
    external_file: str
    partner_equivalent: str | None  # None = gap (Partner doesn't have this)
    gap_description: str
    suggested_target: str
    suggestion_detail: str = ""
    risk: str = "medium"  # "low" | "medium" | "high"


@dataclass
class ImprovementPlan:
    """A concrete improvement plan with real file paths, consumable by SelfEvolveEngine."""

    id: str
    target_module: str
    change_type: str  # "modify_function" | "new_class" | "config_change" | "new_file"
    function_name: str
    new_code: str
    description: str
    risk_level: str  # "low" | "medium" | "high"


# ═══════════════════════════════════════════════════════════════════════════════
# Core Module
# ═══════════════════════════════════════════════════════════════════════════════


class CodeKnowledge:
    """Analyze frontend code from external projects, compare with Partner's GUI,
    and generate concrete improvement plans with real file paths."""

    def __init__(self, partner_root: str | Path = PARTNER_GUI_ROOT):
        self._partner_root = Path(partner_root)
        self._plan_counter = 0

    # ── Public API ─────────────────────────────────────────────────────────

    def fetch_repo(self, source: str) -> str:
        """Resolve a named source to a local filesystem path.

        Args:
            source: "Hermes" or "OpenClaw" (case-insensitive).

        Returns:
            Absolute path to the source root directory.

        Raises:
            ValueError: If the source name is unknown.
            FileNotFoundError: If the resolved path does not exist on disk.
        """
        for key, path in KNOWN_SOURCES.items():
            if key.lower() == source.lower():
                resolved = Path(path).resolve()
                if not resolved.exists():
                    raise FileNotFoundError(
                        f"Source '{source}' resolves to {resolved} but it does not exist"
                    )
                logger.info("Resolved source '%s' → %s", source, resolved)
                return str(resolved)

        valid = ", ".join(KNOWN_SOURCES.keys())
        raise ValueError(
            f"Unknown source '{source}'. Valid sources: {valid}"
        )

    def analyze_frontend_structure(self, path: str) -> dict[str, Any]:
        """Scan a frontend directory tree and return a structured summary.

        Identifies:
          - UI component files (widgets.py, *.tsx, *.vue, etc.)
          - Style/theme files
          - Layout files
          - Page/view files

        Returns:
            dict with keys: source_root, framework, files (list of FileCategory),
            file_count, component_count, page_count, style_count.
        """
        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(f"Path does not exist: {path}")

        framework = self._detect_framework(root)
        categories: list[FileCategory] = []
        seen_dirs: set[str] = set()

        # Use os.walk for efficiency — avoids traversing into excluded dirs
        for dirpath, dirnames, filenames in os.walk(str(root)):
            # Prune excluded directories in-place to prevent os.walk descending into them
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".")
                and d not in ("node_modules", "__pycache__", ".git", "dist", "build")
            ]
            for fname in sorted(filenames):
                file_path = Path(dirpath) / fname
                ext = file_path.suffix.lower()
                if ext not in (
                    ".py",
                    ".tsx",
                    ".ts",
                    ".jsx",
                    ".js",
                    ".vue",
                    ".svelte",
                    ".css",
                    ".scss",
                    ".qss",
                ):
                    continue
                rel = str(file_path.relative_to(root))

                # Read file ONCE and extract all info
                content = self._safe_read(file_path)
                if not content:
                    continue
                lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                classes = self._extract_classes_from_content(content, ext)
                functions = self._extract_functions_from_content(content, ext)

                file_type = self._categorize_file(rel, ext)
                summary = self._summarize_file(rel, classes, functions)

                categories.append(
                    FileCategory(
                        path=rel,
                        file_type=file_type,
                        lines=lines,
                        classes=classes,
                        functions=functions,
                        summary=summary,
                        content=content,
                    )
                )
                # Collect any unique dirs
                dirs = rel.split("/")
                for i in range(len(dirs) - 1):
                    seen_dirs.add("/".join(dirs[: i + 1]))

        result: dict[str, Any] = {
            "source_root": str(root),
            "framework": framework,
            "files": [vars(c) for c in categories],
            "file_count": len(categories),
            "component_count": sum(
                1 for c in categories if c.file_type == "widget"
            ),
            "page_count": sum(1 for c in categories if c.file_type == "page"),
            "style_count": sum(1 for c in categories if c.file_type == "style"),
            "layout_count": sum(1 for c in categories if c.file_type == "layout"),
            "directories": sorted(seen_dirs),
        }
        logger.info(
            "Analyzed %s: %d files, %d components, %d pages, %d styles",
            framework,
            result["file_count"],
            result["component_count"],
            result["page_count"],
            result["style_count"],
        )
        return result

    def extract_ui_patterns(self, path: str) -> UIPattern:
        """Extract concrete UI patterns from a frontend directory.

        Returns a UIPattern with:
          - Component hierarchy (what widgets exist)
          - Layout approach (flexbox, grid, absolute)
          - Styling mechanism (QSS, CSS-in-JS, stylesheet)
          - Interaction patterns (click, keyboard, scroll)
        """
        structure = self.analyze_frontend_structure(path)
        framework = structure["framework"]
        source = Path(path).name

        components: list[ComponentInfo] = []
        layout_keywords: set[str] = set()
        interaction_keywords: set[str] = set()
        styling_keywords: set[str] = set()

        for fc_raw in structure["files"]:
            fc = FileCategory(**fc_raw)
            file_path = Path(path) / fc.path

            if fc.file_type in ("widget", "page", "layout"):
                comps = self._extract_component_info(
                    file_path, framework, fc.path, content=fc.content
                )
                components.extend(comps)

            # Detect layout patterns from code (use cached content)
            content = fc.content
            if content:
                # Layout approach
                for kw in (
                    "flex",
                    "grid",
                    "hbox",
                    "vbox",
                    "qboxlayout",
                    "hboxlayout",
                    "vboxlayout",
                    "qgridlayout",
                    "qsplitter",
                    "qstackedwidget",
                    "stacked",
                    "splitter",
                    "flexbox",
                    "flex-direction",
                    "yogalayout",
                ):
                    if kw.lower() in content.lower():
                        layout_keywords.add(kw)

                # Interaction patterns
                for kw in (
                    "click",
                    "clicked",
                    "onclick",
                    "keypress",
                    "keydown",
                    "keyrelease",
                    "keyboard",
                    "scroll",
                    "mousemove",
                    "mousepress",
                    "mousehover",
                    "drago",
                    "drop",
                    "onkeydown",
                    "onkeypress",
                    "onmousedown",
                    "onmouseenter",
                    "onmouseleave",
                    "onfocus",
                    "onblur",
                ):
                    if kw.lower() in content.lower():
                        interaction_keywords.add(kw)

                # Styling mechanism
                for kw in (
                    "setstylesheet",
                    "stylesheet",
                    "qss",
                    "style.",
                    "styles",
                    "css",
                    "color:",
                    "background-color",
                    "border-radius",
                    "inline style",
                ):
                    if kw.lower() in content.lower():
                        styling_keywords.add(kw)

        # Deduce layout approach
        layout_approach = self._deduce_layout_approach(list(layout_keywords))

        # Deduce styling mechanism
        styling_mechanism = self._deduce_styling_mechanism(
            list(styling_keywords), framework
        )

        pattern = UIPattern(
            source=source,
            framework=framework,
            component_hierarchy=components,
            layout_approach=layout_approach,
            styling_mechanism=styling_mechanism,
            interaction_patterns=sorted(set(interaction_keywords)),
            component_count=len(components),
            file_count=structure["file_count"],
        )
        logger.info(
            "Extracted %d UI components from %s (%s)",
            len(components),
            source,
            framework,
        )
        return pattern

    def compare_with_partner(
        self,
        patterns: UIPattern,
        partner_path: str | None = None,
    ) -> list[DiffEntry]:
        """Compare external UI patterns against Partner's actual GUI.

        Maps external components to Partner equivalents (if they exist), finds
        gaps, and generates concrete diff suggestions with real file paths
        under shells/frontend/desktop_gui/modern/.

        Args:
            patterns: UIPattern extracted from an external source.
            partner_path: Override path to Partner GUI (default: PARTNER_GUI_ROOT).

        Returns:
            List of DiffEntry objects describing each difference found.
        """
        p_root = Path(partner_path) if partner_path else self._partner_root
        diffs: list[DiffEntry] = []
        source_name = patterns.source

        # Analyze Partner's own patterns for comparison
        try:
            partner_structure = self.analyze_frontend_structure(str(p_root))
        except FileNotFoundError:
            logger.warning("Partner GUI not found at %s, using empty baseline", p_root)
            partner_structure = {"files": [], "component_count": 0, "page_count": 0}

        partner_files = {f["path"] for f in partner_structure["files"]}
        partner_classes = self._collect_classes_from_structure(partner_structure)

        # ── Gap analysis per external component ──
        for comp in patterns.component_hierarchy:

            # Map external component type → Partner equivalent file
            partner_eq, target_file = self._map_to_partner(comp, partner_files)

            if partner_eq is None:
                # This is a gap — Partner doesn't have this feature
                gap_desc = self._build_gap_description(comp, source_name)
                risk = self._assess_risk(comp)
                diffs.append(
                    DiffEntry(
                        external_component=comp.name,
                        external_file=comp.file_path,
                        partner_equivalent=None,
                        gap_description=gap_desc,
                        suggested_target=target_file,
                        suggestion_detail=self._build_suggestion_detail(
                            comp, source_name
                        ),
                        risk=risk,
                    )
                )
            else:
                # Partner has an equivalent — check for enhancement opportunities
                enhancement = self._check_enhancement(comp, partner_eq, source_name)
                if enhancement:
                    diffs.append(enhancement)

        # ── Structural gaps ──
        structural_gaps = self._find_structural_gaps(
            patterns, partner_structure, partner_classes, source_name
        )
        diffs.extend(structural_gaps)

        # ── Interaction pattern gaps ──
        interaction_gaps = self._find_interaction_gaps(patterns, partner_files)
        diffs.extend(interaction_gaps)

        # Deduplicate by suggested_target
        seen_targets: set[str] = set()
        unique_diffs: list[DiffEntry] = []
        for d in diffs:
            if d.suggested_target not in seen_targets:
                seen_targets.add(d.suggested_target)
                unique_diffs.append(d)
            else:
                # Merge details into existing entry for same target
                for existing in unique_diffs:
                    if existing.suggested_target == d.suggested_target:
                        existing.gap_description += "; " + d.gap_description
                        break

        logger.info(
            "Comparison complete: %d unique diffs from %s",
            len(unique_diffs),
            source_name,
        )
        return unique_diffs

    def generate_frontend_improvements(
        self, diff_list: list[DiffEntry]
    ) -> list[ImprovementPlan]:
        """Convert each diff into an improvement plan with real file paths
        and concrete Python code suggestions.

        Args:
            diff_list: List of DiffEntry objects from compare_with_partner().

        Returns:
            List of ImprovementPlan objects, each consumable by SelfEvolveEngine.
        """
        plans: list[ImprovementPlan] = []

        for diff in diff_list:
            self._plan_counter += 1
            plan_id = f"plan_f{self._plan_counter:03d}"

            plan = self._diff_to_plan(diff, plan_id)
            if plan:
                plans.append(plan)

        logger.info("Generated %d improvement plans", len(plans))
        return plans

    # ── Internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _detect_framework(root: Path) -> str:
        """Detect the UI framework used in a directory.
        Uses targeted os.walk instead of rglob for performance.
        """
        has_tsx = False
        has_py = False
        has_vue = False
        has_svelte = False
        found_ink = False

        for dirpath, dirnames, filenames in os.walk(str(root)):
            # Prune excluded dirs
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".")
                and d not in ("node_modules", "__pycache__", ".git", "dist", "build")
            ]
            for fname in filenames:
                if fname.endswith(".tsx"):
                    has_tsx = True
                    # Quick check for React-Ink
                    if not found_ink:
                        try:
                            with open(
                                os.path.join(dirpath, fname),
                                "r",
                                encoding="utf-8",
                                errors="replace",
                            ) as f:
                                head = f.read(4096)
                                if "ink" in head.lower() or "react" in head.lower():
                                    found_ink = True
                        except Exception:
                            pass
                    if has_py and has_tsx and found_ink:
                        break
                elif fname.endswith(".vue"):
                    has_vue = True
                elif fname.endswith(".svelte"):
                    has_svelte = True
                elif fname.endswith(".py"):
                    has_py = True

            if has_tsx and found_ink:
                break

        if has_tsx and found_ink:
            return "React-Ink (TypeScript)"
        if has_tsx:
            return "React (TypeScript)"
        if has_vue:
            return "Vue.js"
        if has_svelte:
            return "Svelte"
        if has_py:
            # Check for PySide6 more quickly
            for dirpath, dirnames, filenames in os.walk(str(root)):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not d.startswith(".")
                    and d not in ("node_modules", "__pycache__", ".git", "dist", "build")
                ]
                for fname in filenames:
                    if fname.endswith(".py"):
                        try:
                            with open(
                                os.path.join(dirpath, fname),
                                "r",
                                encoding="utf-8",
                                errors="replace",
                            ) as f:
                                head = f.read(4096)
                                if "PySide6" in head or "PyQt" in head:
                                    return "PySide6 Qt"
                        except Exception:
                            pass
            return "Python"
        return "Unknown"

    @staticmethod
    def _categorize_file(rel_path: str, ext: str) -> str:
        """Categorise a file by its path and extension."""
        name_lower = Path(rel_path).stem.lower()
        path_lower = rel_path.lower()

        # Check against patterns
        for file_type, pattern in UI_FILE_PATTERNS.items():
            if pattern.search(name_lower) or pattern.search(path_lower):
                return file_type

        return "other"

    @staticmethod
    def _count_lines(file_path: Path) -> int:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    @staticmethod
    def _safe_read(file_path: Path) -> str:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return ""

    @staticmethod
    def _extract_classes_from_content(content: str, ext: str) -> list[str]:
        """Extract class names from file content string (single read)."""
        if not content:
            return []
        classes = []
        if ext == ".py":
            for m in re.finditer(
                r"^class\s+(\w+)", content, re.MULTILINE
            ):
                classes.append(m.group(1))
        elif ext in (".tsx", ".ts", ".jsx", ".js"):
            for m in re.finditer(
                r"(?:export\s+)?(?:default\s+)?(?:class|function)\s+(\w+)",
                content,
            ):
                classes.append(m.group(1))
            # React components as const arrow functions
            for m in re.finditer(
                r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=]\s*(?:React\.memo\s*\(\s*)?(?:function\s*)?\(",
                content,
            ):
                name = m.group(1)
                if name[0].isupper() or name == "default":
                    classes.append(name)
        return sorted(set(classes))

    @staticmethod
    def _extract_functions_from_content(content: str, ext: str) -> list[str]:
        """Extract function/method names from file content string (single read)."""
        if not content:
            return []
        functions = []
        if ext == ".py":
            for m in re.finditer(
                r"^(?:\s*)def\s+(\w+)\s*\(", content, re.MULTILINE
            ):
                functions.append(m.group(1))
        elif ext in (".tsx", ".ts", ".jsx", ".js"):
            for m in re.finditer(
                r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
                content,
            ):
                functions.append(m.group(1))
            for m in re.finditer(
                r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(",
                content,
            ):
                functions.append(m.group(1))
        return sorted(set(functions))

    @staticmethod
    def _extract_classes(file_path: Path) -> list[str]:
        """Extract class names from a Python or TypeScript file."""
        content = CodeKnowledge._safe_read(file_path)
        if not content:
            return []

        classes = []
        ext = file_path.suffix.lower()

        if ext == ".py":
            for m in re.finditer(
                r"^class\s+(\w+)", content, re.MULTILINE
            ):
                classes.append(m.group(1))
        elif ext in (".tsx", ".ts", ".jsx", ".js"):
            for m in re.finditer(
                r"(?:export\s+)?(?:default\s+)?(?:class|function)\s+(\w+)",
                content,
            ):
                classes.append(m.group(1))
            # React components as const arrow functions
            for m in re.finditer(
                r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=]\s*(?:React\.memo\s*\(\s*)?(?:function\s*)?\(",
                content,
            ):
                name = m.group(1)
                if name[0].isupper() or name == "default":
                    classes.append(name)

        return sorted(set(classes))

    @staticmethod
    def _extract_functions(file_path: Path) -> list[str]:
        """Extract function/method names."""
        content = CodeKnowledge._safe_read(file_path)
        if not content:
            return []

        functions = []
        ext = file_path.suffix.lower()

        if ext == ".py":
            for m in re.finditer(
                r"^def\s+(\w+)\s*\(", content, re.MULTILINE
            ):
                functions.append(m.group(1))
            for m in re.finditer(
                r"^\s+def\s+(\w+)\s*\(", content, re.MULTILINE
            ):
                functions.append(m.group(1))
        elif ext in (".tsx", ".ts", ".jsx", ".js"):
            for m in re.finditer(
                r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
                content,
            ):
                functions.append(m.group(1))
            # Arrow functions assigned to const
            for m in re.finditer(
                r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(",
                content,
            ):
                functions.append(m.group(1))

        return sorted(set(functions))

    @staticmethod
    def _summarize_file(
        rel_path: str, classes: list[str], functions: list[str]
    ) -> str:
        parts = []
        if classes:
            cls_list = ", ".join(classes[:5])
            if len(classes) > 5:
                cls_list += f" ... (+{len(classes)-5})"
            parts.append(f"classes: [{cls_list}]")
        if functions:
            fn_list = ", ".join(functions[:5])
            if len(functions) > 5:
                fn_list += f" ... (+{len(functions)-5})"
            parts.append(f"functions: [{fn_list}]")
        if not parts:
            return rel_path
        return f"{rel_path} — {'; '.join(parts)}"

    def _extract_component_info(
        self, file_path: Path, framework: str, rel_path: str,
        content: str = "",
    ) -> list[ComponentInfo]:
        """Extract ComponentInfo objects from a source file."""
        if not content:
            content = self._safe_read(file_path)
        if not content:
            return []

        components: list[ComponentInfo] = []
        classes = self._extract_classes_from_content(content, file_path.suffix.lower())
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

        for cls_name in classes:
            widget_type = self._infer_widget_type(cls_name, content, framework)
            info = ComponentInfo(
                name=cls_name,
                file_path=rel_path,
                framework=framework,
                widget_type=widget_type,
                has_hover="hover" in content.lower(),
                has_click="click" in content.lower(),
                has_keyboard="key" in content.lower(),
                has_expand="expand" in content.lower(),
                has_scroll="scroll" in content.lower(),
                has_animation="anim" in content.lower() or "transition" in content.lower(),
                line_count=lines,
            )
            components.append(info)

        return components

    @staticmethod
    def _infer_widget_type(name: str, content: str, framework: str) -> str:
        """Infer what kind of UI widget this is from its name and code context."""
        name_lower = name.lower()

        if any(kw in name_lower for kw in ("button", "btn", "action")):
            return "button"
        if any(kw in name_lower for kw in ("input", "textarea", "textedit", "lineedit", "combo")):
            return "input"
        if any(kw in name_lower for kw in ("card", "bubble", "item", "entry")):
            return "card"
        if any(kw in name_lower for kw in ("dialog", "modal", "popup", "alert", "message")):
            return "dialog"
        if any(kw in name_lower for kw in ("nav", "sidebar", "tab", "menu")):
            return "navigation"
        if any(kw in name_lower for kw in ("scroll", "list", "tree", "table")):
            return "list"
        if any(kw in name_lower for kw in ("container", "box", "layout", "panel", "split", "frame")):
            return "container"
        if any(kw in name_lower for kw in ("label", "text", "icon", "display", "badge", "status")):
            return "display"

        # If it has QPushButton/QWidget/QFrame superclass patterns, use that
        if "qpushbutton" in content.lower() or "qframe" in content.lower():
            return "container"

        return "unknown"

    @staticmethod
    def _deduce_layout_approach(keywords: list[str]) -> str:
        """Deduce layout approach from extracted keywords."""
        kw = [k.lower() for k in keywords]

        if any("yoga" in k for k in kw):
            return "Yoga flexbox layout"
        if "flex" in kw or "flexbox" in kw or "flex-direction" in kw:
            return "Flexbox layout"
        if any("grid" in k for k in kw) or "qgridlayout" in kw:
            return "Grid layout"
        if any("hbox" in k for k in kw) or "hboxlayout" in kw:
            return "Horizontal/Vertical Box layout"
        if "qsplitter" in kw:
            return "Splitter-based layout"
        if "qstackedwidget" in kw or "stacked" in kw:
            return "Stacked/pages layout"

        return "Unknown"

    @staticmethod
    def _deduce_styling_mechanism(
        keywords: list[str], framework: str
    ) -> str:
        """Deduce styling mechanism."""
        kw = [k.lower() for k in keywords]

        if "qss" in kw or "setstylesheet" in kw or "stylesheet" in kw:
            return "QSS (Qt Style Sheets)"
        if "style." in kw or "styles" in kw or framework == "React-Ink (TypeScript)":
            return "CSS-in-JS / inline style objects"
        if "css" in kw and "py" not in framework.lower():
            return "CSS stylesheets"
        if "color:" in kw or "background-color" in kw:
            return "Inline styles / style attributes"

        return "Unknown"

    def _collect_classes_from_structure(
        self, structure: dict[str, Any]
    ) -> dict[str, str]:
        """Build a dict: class_name → file_path from a structure analysis."""
        result: dict[str, str] = {}
        for fc_raw in structure.get("files", []):
            fc = FileCategory(**fc_raw)
            for cls in fc.classes:
                result[cls] = fc.path
        return result

    def _map_to_partner(
        self, comp: ComponentInfo, partner_files: set[str]
    ) -> tuple[str | None, str]:
        """Map an external component to Partner's equivalent file.

        Returns (partner_equivalent_path_or_None, suggested_target_path).
        """
        comp_lower = comp.name.lower()
        p_root = self._partner_root

        # Direct widget class name mapping
        widget_mapping = {
            # External (Hermes / generic) → Partner equivalent
            "button": "widgets.py",
            "text": "widgets.py",
            "box": "widgets.py",
            "scrollbox": "widgets.py",
            "spacer": "widgets.py",
            "newline": "widgets.py",
            "link": "widgets.py",
            "erroroverview": "widgets.py",
            "alternatescreen": "main_window.py",
            "chatbubble": "widgets.py",
            "eventcard": "widgets.py",
            "eventstepwidget": "widgets.py",
            "accentsection": "widgets.py",
            "sectionheader": "widgets.py",
            "accentbutton": "widgets.py",
            "collapsibleconfiggroup": "widgets.py",
            "dirbrowser": "widgets.py",
            "statuscard": "widgets.py",
        }

        # Check direct mapping first
        for key, val in widget_mapping.items():
            if key in comp_lower:
                partner_path_for_target = f"pages/{val}" if val.startswith("pages/") else val
                full_partner_path = str(p_root / partner_path_for_target)

                # Check if file exists
                if (p_root / partner_path_for_target).exists():
                    return full_partner_path, partner_path_for_target

        # Check Partner code for class name matches
        for pf in partner_files:
            pf_lower = pf.lower()
            # If the component name appears in Partner's file paths
            if comp_lower.split(".")[0] in pf_lower:
                full_path = str(p_root / pf)
                return full_path, pf

        # If we're a page component, suggest the corresponding page file
        if comp.widget_type in ("navigation", "page"):
            suggested = f"pages/{comp.name.lower()}.py"
            if (p_root / suggested).exists():
                return str(p_root / suggested), suggested

        # If it's a button/card type, suggest widgets.py as the target
        if comp.widget_type in ("button", "card", "container", "input", "display"):
            target = "widgets.py"
            return None, target

        # Default suggestion
        return None, "widgets.py"

    def _build_gap_description(
        self, comp: ComponentInfo, source_name: str
    ) -> str:
        """Build a description of the gap for a component missing in Partner."""
        parts = [
            f"{source_name} has '{comp.name}' ({comp.widget_type})"
        ]
        features = []
        if comp.has_hover:
            features.append("hover effects")
        if comp.has_click:
            features.append("click handling")
        if comp.has_keyboard:
            features.append("keyboard support")
        if comp.has_expand:
            features.append("expand/collapse")
        if comp.has_scroll:
            features.append("scrolling")
        if comp.has_animation:
            features.append("animations")

        if features:
            parts.append(f"with {', '.join(features)}")
        parts.append("— Partner GUI has no equivalent")

        return " ".join(parts)

    def _build_suggestion_detail(
        self, comp: ComponentInfo, source_name: str
    ) -> str:
        """Build a detailed suggestion for implementing the gap."""
        from_file = comp.file_path
        return (
            f"Inspired by {source_name}/{from_file}. "
            f"Implement {comp.name} as a PySide6 QWidget subclass "
            f"with proper QSS styling, following the existing patterns "
            f"in widgets.py (AccentButton, EventCard)."
        )

    def _assess_risk(self, comp: ComponentInfo) -> str:
        """Assess risk level of implementing a component."""
        if comp.line_count > 300:
            return "high"
        if comp.widget_type in ("button", "display", "input"):
            return "low"
        if comp.widget_type in ("card", "container", "dialog"):
            return "medium"
        return "medium"

    def _check_enhancement(
        self, comp: ComponentInfo, partner_eq: str, source_name: str
    ) -> DiffEntry | None:
        """Check if a Partner equivalent could be enhanced based on external pattern."""
        comp_lower = comp.name.lower()
        p_root = self._partner_root

        # Determine the relative path from the full partner_eq path
        rel_path = partner_eq
        if str(p_root) in partner_eq:
            rel_path = os.path.relpath(partner_eq, str(p_root))

        # Check for specific enhancement opportunities
        enhancements: list[tuple[str, str, str]] = []

        # 1. ChatBubble content rendering enhancement (Hermes Ansi.tsx → Markdown/ANSI rendering)
        if "chatbubble" in comp_lower or ("bubble" in comp_lower and source_name.lower() == "hermes"):
            enhancements.append(
                (
                    "Add ANSI color code rendering support to ChatBubble._render_content",
                    "shells/frontend/desktop_gui/modern/widgets.py",
                    "medium",
                )
            )

        # 2. ScrollBox auto-scroll and sticky behavior (Hermes ScrollBox.tsx)
        if "scroll" in comp_lower or "scrollbox" in comp_lower:
            enhancements.append(
                (
                    "Add sticky-scroll (auto-follow bottom) behavior to chat scroll area",
                    "shells/frontend/desktop_gui/modern/pages/chat.py",
                    "medium",
                )
            )

        # 3. Keyboard navigation (Hermes Box.tsx tabIndex/onKeyDown)
        if "box" in comp_lower or "button" in comp_lower:
            enhancements.append(
                (
                    "Add keyboard navigation (Tab/Enter/Escape) to chat input area",
                    "shells/frontend/desktop_gui/modern/widgets.py",
                    "low",
                )
            )

        # 4. EventStepWidget expandable detail (inspired by Hermes collapsible patterns)
        if "step" in comp_lower or "expand" in comp_lower:
            enhancements.append(
                (
                    "Add animated expand/collapse transition to EventStepWidget",
                    "shells/frontend/desktop_gui/modern/widgets.py",
                    "low",
                )
            )

        # 5. ANSI / syntax highlighting (Hermes Ansi.tsx, RawAnsi.tsx)
        if "ansi" in comp_lower or "raw" in comp_lower or "highlight" in comp_lower:
            enhancements.append(
                (
                    "Add raw ANSI output rendering for terminal log views (like RawAnsi.tsx)",
                    "shells/frontend/desktop_gui/modern/widgets.py",
                    "high",
                )
            )

        if enhancements:
            desc, target, risk = enhancements[0]
            return DiffEntry(
                external_component=comp.name,
                external_file=comp.file_path,
                partner_equivalent=rel_path,
                gap_description=desc,
                suggested_target=target,
                suggestion_detail=f"Enhance existing {rel_path} with patterns from {source_name}/{comp.file_path}",
                risk=risk,
            )

        return None

    def _find_structural_gaps(
        self,
        patterns: UIPattern,
        partner_structure: dict[str, Any],
        partner_classes: dict[str, str],
        source_name: str,
    ) -> list[DiffEntry]:
        """Find gaps in Partner's structural/architectural patterns vs external."""
        diffs: list[DiffEntry] = []
        p_root = self._partner_root

        partner_component_count = partner_structure.get("component_count", 0)
        partner_page_count = partner_structure.get("page_count", 0)

        # Check if external has significantly more components
        if patterns.component_count > partner_component_count * 1.5:
            # Suggest adding new UI components
            diffs.append(
                DiffEntry(
                    external_component="(structural)",
                    external_file="",
                    partner_equivalent=None,
                    gap_description=(
                        f"{source_name} has {patterns.component_count} components "
                        f"vs Partner's {partner_component_count}. "
                        f"Consider adding reusable widget components."
                    ),
                    suggested_target="shells/frontend/desktop_gui/modern/widgets.py",
                    suggestion_detail=(
                        f"Based on {source_name}'s component architecture, "
                        f"add new reusable widgets to widgets.py following the "
                        f"AccentButton/EventCard pattern."
                    ),
                    risk="medium",
                )
            )

        # Layout approach comparison
        ext_layout = patterns.layout_approach.lower()
        partner_layout = self._get_partner_layout_approach(p_root)

        if "flexbox" in ext_layout and "box" not in partner_layout:
            diffs.append(
                DiffEntry(
                    external_component="(layout)",
                    external_file="",
                    partner_equivalent=None,
                    gap_description=(
                        f"{source_name} uses {patterns.layout_approach} "
                        f"(more dynamic than current {partner_layout}). "
                        f"Add flexible layout components to improve responsiveness."
                    ),
                    suggested_target="shells/frontend/desktop_gui/modern/widgets.py",
                    suggestion_detail=(
                        f"Create a FlexContainer widget that wraps QVBoxLayout/QHBoxLayout "
                        f"with stretch factors, inspired by {source_name}'s Box component."
                    ),
                    risk="low",
                )
            )

        # Interaction richness comparison
        ext_interactions = set(patterns.interaction_patterns)
        partner_interactions = self._detect_partner_interactions(p_root)

        missing_interactions = ext_interactions - partner_interactions
        if missing_interactions:
            diffs.append(
                DiffEntry(
                    external_component="(interaction)",
                    external_file="",
                    partner_equivalent=None,
                    gap_description=(
                        f"{source_name} supports: {', '.join(sorted(missing_interactions))} "
                        f"— Partner GUI lacks these interaction patterns. "
                        f"Enhance keyboard navigation and event handling."
                    ),
                    suggested_target="shells/frontend/desktop_gui/modern/widgets.py",
                    suggestion_detail=(
                        f"Add {', '.join(sorted(missing_interactions))} "
                        f"event handling to input widgets, inspired by {source_name}."
                    ),
                    risk="medium",
                )
            )

        return diffs

    def _find_interaction_gaps(
        self,
        patterns: UIPattern,
        partner_files: set[str],
    ) -> list[DiffEntry]:
        """Find specific interaction pattern gaps."""
        diffs: list[DiffEntry] = []
        p_root = self._partner_root

        # Check if Partner has keyboard event handling
        chat_file = p_root / "pages" / "chat.py"
        widgets_file = p_root / "widgets.py"

        chat_content = self._safe_read(chat_file)
        widgets_content = self._safe_read(widgets_file)

        has_keyboard_handler = "keyPressEvent" in chat_content or "keyPressEvent" in widgets_content
        has_drag_drop = "dragEnterEvent" in chat_content or "dropEvent" in chat_content
        has_signal_system = "Signal(" in widgets_content or "Signal(" in chat_content

        # Check if external has keyboard focus management
        ext_has_keyboard = any(c.has_keyboard for c in patterns.component_hierarchy)

        if ext_has_keyboard and not has_keyboard_handler:
            diffs.append(
                DiffEntry(
                    external_component="(keyboard)",
                    external_file="",
                    partner_equivalent=None,
                    gap_description=(
                        f"External source has keyboard focus management and event handling, "
                        f"but Partner's input area lacks keyboard interaction handler. "
                        f"Add keyPressEvent for Enter-to-send, Escape-to-cancel."
                    ),
                    suggested_target="shells/frontend/desktop_gui/modern/pages/chat.py",
                    suggestion_detail=(
                        "Add keyPressEvent override to the chat input QPlainTextEdit "
                        "to handle Enter (send), Shift+Enter (newline), Escape (clear)."
                    ),
                    risk="low",
                )
            )

        return diffs

    def _diff_to_plan(
        self, diff: DiffEntry, plan_id: str
    ) -> ImprovementPlan | None:
        """Convert a DiffEntry into a concrete ImprovementPlan with real Python code."""
        target = diff.suggested_target

        # Ensure the target starts with shells/frontend/... relative path
        if not target.startswith("shells/frontend/"):
            target = f"shells/frontend/desktop_gui/modern/{target.lstrip('/')}"

        change_type, function_name, new_code = self._synthesize_implementation(
            diff, target
        )

        return ImprovementPlan(
            id=plan_id,
            target_module=target,
            change_type=change_type,
            function_name=function_name,
            new_code=new_code,
            description=diff.gap_description,
            risk_level=diff.risk,
        )

    def _synthesize_implementation(
        self, diff: DiffEntry, target: str
    ) -> tuple[str, str, str]:
        """Synthesize actual Python code for an improvement."""
        comp_name = diff.external_component
        desc_lower = diff.gap_description.lower()

        # ── Keyboard event handler for chat input ──
        if "keyboard" in desc_lower or "keypressevent" in desc_lower:
            return (
                "modify_function",
                "InputArea.keyPressEvent",
                (
                    "def keyPressEvent(self, event):\n"
                    '    """Handle keyboard shortcuts: Enter to send, Escape to clear."""\n'
                    "    from PySide6.QtCore import Qt\n"
                    "    if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:\n"
                    "        modifiers = event.modifiers()\n"
                    "        if modifiers == Qt.KeyboardModifier.ShiftModifier:\n"
                    "            # Shift+Enter = insert newline (default behavior)\n"
                    "            super().keyPressEvent(event)\n"
                    "        else:\n"
                    "            # Enter = send\n"
                    "            self.send_signal.emit()\n"
                    "    elif event.key() == Qt.Key.Key_Escape:\n"
                    "        # Escape = clear input\n"
                    "        self.clear()\n"
                    "    else:\n"
                    "        super().keyPressEvent(event)\n"
                ),
            )

        # ── Sticky scroll for chat area ──
        if "sticky" in desc_lower or "scroll" in desc_lower and "auto" in desc_lower:
            return (
                "modify_function",
                "ChatPage._setup_scroll_area",
                (
                    "def _setup_scroll_area(self):\n"
                    '    """Set up the chat scroll area with sticky-bottom auto-follow."""\n'
                    "    self._scroll_area = QScrollArea()\n"
                    "    self._scroll_area.setWidgetResizable(True)\n"
                    "    self._scroll_area.setVerticalScrollBarPolicy(\n"
                    "        Qt.ScrollBarPolicy.ScrollBarAsNeeded\n"
                    "    )\n"
                    "    self._scroll_area.setHorizontalScrollBarPolicy(\n"
                    "        Qt.ScrollBarPolicy.ScrollBarAlwaysOff\n"
                    "    )\n"
                    "    self._scroll_area.setStyleSheet(\n"
                    '        f"QScrollArea {{ border: none; background: transparent; }}"\n'
                    "    )\n"
                    "    # Sticky-scroll tracking\n"
                    "    self._auto_scroll = True\n"
                    "    scrollbar = self._scroll_area.verticalScrollBar()\n"
                    "    scrollbar.valueChanged.connect(self._on_scroll_changed)\n"
                    "\n"
                    "def _on_scroll_changed(self, value: int):\n"
                    '    """Track whether user is at bottom (auto-scroll) or has scrolled up."""\n'
                    "    scrollbar = self._scroll_area.verticalScrollBar()\n"
                    "    at_bottom = (value >= scrollbar.maximum() - 10)\n"
                    "    self._auto_scroll = at_bottom\n"
                    "\n"
                    "def _scroll_to_bottom(self):\n"
                    '    """Scroll to bottom if auto-scroll is enabled."""\n'
                    "    if self._auto_scroll:\n"
                    "        scrollbar = self._scroll_area.verticalScrollBar()\n"
                    "        scrollbar.setValue(scrollbar.maximum())\n"
                ),
            )

        # ── ChatBubble ANSI rendering ──
        if "ansi" in desc_lower:
            return (
                "modify_function",
                "ChatBubble._render_content",
                (
                    "def _render_content(self, text: str) -> None:\n"
                    '    """Render content with ANSI color code support.\n'
                    "\n"
                    "    Parses ANSI escape sequences in assistant messages and\n"
                    "    converts them to colored HTML spans using QLabel's RichText.\n"
                    '    Falls back to _md_to_html for non-ANSI text.\n'
                    '    """\n'
                    "    import re as _re\n"
                    "    # Check for ANSI escape codes\n"
                    '    if "\\x1b[" in text:\n'
                    "        html = self._ansi_to_html(text)\n"
                    "        self.content_label.setTextFormat(Qt.TextFormat.RichText)\n"
                    "        self.content_label.setText(html)\n"
                    "    elif self._role in (\"assistant\", \"bot\"):\n"
                    "        self.content_label.setTextFormat(Qt.TextFormat.RichText)\n"
                    "        self.content_label.setText(_md_to_html(text))\n"
                    "    else:\n"
                    "        self.content_label.setTextFormat(Qt.TextFormat.PlainText)\n"
                    "        self.content_label.setText(text)\n"
                    "\n"
                    "@staticmethod\n"
                    "def _ansi_to_html(ansi_text: str) -> str:\n"
                    '    """Convert ANSI escape codes to HTML spans."""\n'
                    "    import html as _html\n"
                    "    # ANSI color map (standard 8/16 colors)\n"
                    "    ANSI_COLORS = {\n"
                    '        "30": "#2C3E50", "31": "#E53935", "32": "#4CAF50",\n'
                    '        "33": "#F5A623", "34": "#4A90D9", "35": "#E91E63",\n'
                    '        "36": "#00BCD4", "37": "#FFFFFF", "90": "#7F8C8D",\n'
                    '        "91": "#EF5350", "92": "#66BB6A", "93": "#FFCA28",\n'
                    '        "94": "#64B5F6", "95": "#AB47BC", "96": "#4DD0E1",\n'
                    '        "97": "#F5F5F5",\n'
                    "    }\n"
                    "    result = []\n"
                    "    i = 0\n"
                    "    while i < len(ansi_text):\n"
                    '        if ansi_text[i] == "\\x1b" and i + 1 < len(ansi_text) and ansi_text[i + 1] == "[":\n'
                    "            end = ansi_text.find(\"m\", i)\n"
                    "            if end == -1:\n"
                    "                result.append(_html.escape(ansi_text[i:]))\n"
                    "                break\n"
                    "            code = ansi_text[i + 2 : end]\n"
                    "            if code == \"0\" or code == \"\":\n"
                    '                result.append("</span>")\n'
                    "            elif code in ANSI_COLORS:\n"
                    '                result.append(f\'<span style="color:{ANSI_COLORS[code]}">\')\n'
                    "            i = end + 1\n"
                    "        else:\n"
                    "            line_end = ansi_text.find(\"\\n\", i)\n"
                    "            esc_next = ansi_text.find(\"\\x1b\", i)\n"
                    "            if esc_next != -1 and (line_end == -1 or esc_next < line_end):\n"
                    "                chunk = ansi_text[i:esc_next]\n"
                    "                i = esc_next\n"
                    "            else:\n"
                    "                chunk = ansi_text[i:] if line_end == -1 else ansi_text[i:line_end + 1]\n"
                    "                i = line_end + 1 if line_end != -1 else len(ansi_text)\n"
                    "            result.append(_html.escape(chunk))\n"
                    "    return \"\".join(result)\n"
                ),
            )

        # ── Collapsible animation for EventStepWidget ──
        if "expand" in desc_lower or "collapsible" in desc_lower:
            return (
                "modify_function",
                "EventStepWidget._toggle_expand",
                (
                    "def _toggle_expand(self):\n"
                    '    """Toggle the detail section with smooth height animation."""\n'
                    "    self._expanded = not self._expanded\n"
                    "    if self._expanded:\n"
                    "        self._detail_widget.setVisible(True)\n"
                    "        # Animate: start from 0 height, grow to full\n"
                    "        start_h = 0\n"
                    "        end_h = self._detail_widget.sizeHint().height()\n"
                    "        self._animate_height(self._detail_widget, start_h, end_h)\n"
                    "    else:\n"
                    "        start_h = self._detail_widget.height()\n"
                    "        end_h = 0\n"
                    "        self._animate_height(self._detail_widget, start_h, end_h, on_finish=lambda: self._detail_widget.setVisible(False))\n"
                    "\n"
                    "def _animate_height(self, widget, start_h: int, end_h: int,\n"
                    "                     duration: int = 150, on_finish=None):\n"
                    '    """Animate widget height from start_h to end_h over duration ms."""\n'
                    "    from PySide6.QtCore import QPropertyAnimation, QEasingCurve\n"
                    "    anim = QPropertyAnimation(widget, b\"maximumHeight\")\n"
                    "    anim.setDuration(duration)\n"
                    "    anim.setStartValue(start_h)\n"
                    "    anim.setEndValue(end_h)\n"
                    "    anim.setEasingCurve(QEasingCurve.Type.OutCubic)\n"
                    "    if on_finish:\n"
                    "        anim.finished.connect(on_finish)\n"
                    "    anim.start()\n"
                    "    # Store reference to prevent garbage collection\n"
                    "    self._current_anim = anim\n"
                ),
            )

        # ── Dark/light theme toggle ──
        if "theme" in desc_lower or "dark" in desc_lower or "light" in desc_lower:
            return (
                "modify_function",
                "generate_stylesheet",
                (
                    "def generate_stylesheet(dark_mode: bool = False) -> str:\n"
                    '    """Generate the QSS stylesheet for the application.\n'
                    "\n"
                    "    Args:\n"
                    "        dark_mode: If True, generate dark theme stylesheet.\n"
                    '    """\n'
                    "    if dark_mode:\n"
                    "        return _generate_dark_stylesheet()\n"
                    "    return _generate_light_stylesheet()\n"
                    "\n"
                    "\n"
                    "def _generate_dark_stylesheet() -> str:\n"
                    '    """Generate dark mode QSS stylesheet."""\n'
                    "    # Dark theme colors\n"
                    "    bg = \"#1E1E2E\"\n"
                    "    bg2 = \"#2B2B3D\"\n"
                    "    bg3 = \"#363649\"\n"
                    "    card = \"#2B2B3D\"\n"
                    "    txt = \"#CDD6F4\"\n"
                    "    txt2 = \"#A6ADC8\"\n"
                    "    txt3 = \"#6C7086\"\n"
                    "    border = \"#45475A\"\n"
                    "    accent = \"#89B4FA\"\n"
                    "    return f\"\"\"\n"
                    f"    QMainWindow {{ background-color: {bg}; color: {txt}; }}\n"
                    f"    QWidget {{ background-color: {bg}; color: {txt}; }}\n"
                    f"    QLabel {{ background: transparent; color: {txt}; }}\n"
                    f"    QPushButton {{ background-color: {bg2}; color: {txt}; "
                    f"border: 1px solid {border}; border-radius: 10px; "
                    f"padding: 8px 20px; }}\n"
                    f"    QLineEdit, QPlainTextEdit {{ background-color: {bg2}; "
                    f"color: {txt}; border: 1px solid {border}; "
                    f"border-radius: 8px; padding: 8px; }}\n"
                    f"    QScrollArea {{ border: none; background: transparent; }}\n"
                    '    """\n'
                    "\n"
                    "\n"
                    "def _generate_light_stylesheet() -> str:\n"
                    '    """Generate light mode QSS stylesheet (default)."""\n'
                    "    T = THEME\n"
                    "    return f\"\"\"\\n\"\"\"  # Original stylesheet continues...\n"
                ),
            )

        # ── Generic new widget class ──
        parts = comp_name.split(".")
        class_name = parts[0] if parts else "NewWidget"

        return (
            "new_class",
            class_name,
            (
                f"class {class_name}(QFrame):\n"
                f'    """A new widget component inspired by {diff.external_file}.\n'
                "\n"
                f"    {diff.suggestion_detail}\n"
                '    """\n'
                "\n"
                "    def __init__(self, parent: QWidget | None = None):\n"
                "        super().__init__(parent)\n"
                "        self.setObjectName(f\"{class_name.lower()}\")\n"
                "        self._build_ui()\n"
                "\n"
                "    def _build_ui(self):\n"
                '        """Build the widget layout."""\n'
                "        layout = QVBoxLayout(self)\n"
                "        layout.setContentsMargins(12, 10, 12, 10)\n"
                "        layout.setSpacing(8)\n"
                "\n"
                "        self.setStyleSheet(f\"\"\"\n"
                f"            QFrame#{{self.objectName()}} {{\n"
                f"                background-color: {THEME.card};\n"
                f"                border: 1px solid {THEME.border};\n"
                f"                border-radius: 8px;\n"
                "            }}\n"
                f"            QFrame#{{self.objectName()}}:hover {{\n"
                f"                background-color: {THEME.card_hl};\n"
                f"                border-color: {THEME.accent};\n"
                "            }}\n"
                '        """)\n'
            ),
        )

    def _get_partner_layout_approach(self, p_root: Path) -> str:
        """Detect Partner's layout approach from source code."""
        main_window = p_root / "main_window.py"
        content = self._safe_read(main_window)

        if "QStackedWidget" in content:
            return "Stacked widget + box layouts"
        if "QSplitter" in content:
            return "Splitter-based layout"
        if "QGridLayout" in content:
            return "Grid layout"
        if "QVBoxLayout" in content and "QHBoxLayout" in content:
            return "Vertical/Horizontal box layouts"
        return "Unknown"

    def _detect_partner_interactions(self, p_root: Path) -> set[str]:
        """Detect interaction patterns present in Partner GUI."""
        interactions: set[str] = set()
        for py_file in p_root.rglob("*.py"):
            content = self._safe_read(py_file)
            if not content:
                continue
            content_lower = content.lower()
            if "click" in content_lower or "mousepressevent" in content_lower:
                interactions.add("click")
            if "keypressevent" in content_lower or "key" in content_lower:
                interactions.add("keyboard")
            if "scroll" in content_lower:
                interactions.add("scroll")
            if "hover" in content_lower:
                interactions.add("hover")
            if "drago" in content_lower or "drop" in content_lower:
                interactions.add("drag_drop")

        return interactions


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level convenience functions (for use by legacy code/scripts)
# ═══════════════════════════════════════════════════════════════════════════════


def fetch_repo(source: str) -> str:
    """Convenience: resolve a source name to a local path."""
    return CodeKnowledge().fetch_repo(source)


def analyze_frontend(path: str) -> dict[str, Any]:
    """Convenience: analyze a frontend directory structure."""
    return CodeKnowledge().analyze_frontend_structure(path)


def extract_patterns(path: str) -> UIPattern:
    """Convenience: extract UI patterns from a frontend directory."""
    return CodeKnowledge().extract_ui_patterns(path)


def compare_ui(
    patterns: UIPattern, partner_path: str | None = None
) -> list[DiffEntry]:
    """Convenience: compare external patterns against Partner's GUI."""
    return CodeKnowledge().compare_with_partner(patterns, partner_path)


def generate_plans(
    diffs: list[DiffEntry],
) -> list[ImprovementPlan]:
    """Convenience: generate improvement plans from diffs."""
    return CodeKnowledge().generate_frontend_improvements(diffs)
