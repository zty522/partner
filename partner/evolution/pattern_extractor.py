"""Pattern Extractor — extracts reusable design patterns from code.

This module takes a RepositoryKnowledge (produced by CodeLearner) and
identifies reusable design patterns within it. For frontend code, it
extracts layout, interaction, styling, and component patterns. For
general code, it extracts architectural and API patterns.

Each extracted pattern has a structured format:
  {
    "pattern_type": "layout" | "interaction" | "styling" | "component" | "architectural" | "api",
    "name": "Sidebar Navigation",
    "description": "Fixed left sidebar with 240px width, navigation items, and user avatar",
    "implementation": "components/Sidebar.tsx",
    "key_features": ["Collapsible", "Active item highlighting", "Icon + text support"],
    "code_snippet": "...",
    "source": "Hermes",
    "relevance": 0.85,
  }

Usage:
    from partner.evolution.pattern_extractor import PatternExtractor
    from partner.evolution.code_learner import CodeLearner

    knowledge = await CodeLearner.learn(repo_urls=["https://github.com/..."])
    patterns = PatternExtractor.extract(knowledge)
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .code_learner import RepositoryKnowledge

logger = logging.getLogger(__name__)

# ── UI-specific patterns ──────────────────────────────────────────────────────

# Keywords that suggest a layout component
LAYOUT_KEYWORDS: set[str] = {
    "sidebar", "nav", "navigation", "header", "footer", "layout",
    "container", "grid", "flex", "splitter", "panel", "pane",
    "dock", "toolbar", "statusbar", "breadcrumb", "tabs",
    "stacked", "accordion", "section", "frame", "box",
}

# Keywords that suggest an interaction pattern
INTERACTION_KEYWORDS: set[str] = {
    "click", "onclick", "clicked", "keypress", "keydown", "keyup",
    "keyrelease", "onkeydown", "onkeypress", "onkeyup",
    "scroll", "onscroll", "drag", "drop", "dragover", "dragstart",
    "mouseenter", "mouseleave", "mousemove", "hover",
    "focus", "blur", "onfocus", "onblur",
    "change", "input", "submit", "toggle",
    "expand", "collapse", "resize",
    "swipe", "pinch", "zoom",
    "animation", "transition", "transform",
}

# Keywords that suggest a specific component type
COMPONENT_PATTERNS: dict[str, list[str]] = {
    "chat_bubble": ["bubble", "chatbubble", "message", "chat-msg", "chat_msg"],
    "input_box": ["input", "textinput", "text-input", "textinput", "textarea", "chatinput"],
    "button": ["button", "btn", "action-button", "action_btn"],
    "card": ["card", "eventcard", "statuscard", "infocard"],
    "dialog": ["dialog", "modal", "popup", "overlay"],
    "sidebar": ["sidebar", "sidenav", "side-nav"],
    "tabs": ["tab", "tab-bar", "tabpanel"],
    "list": ["list", "listview", "list-view", "table"],
    "dropdown": ["dropdown", "select", "combobox", "combo-box"],
    "avatar": ["avatar", "userpic", "profile-pic"],
    "badge": ["badge", "tag", "chip", "label-tag"],
    "progress": ["progress", "loading", "spinner", "skeleton"],
    "tooltip": ["tooltip", "popover"],
    "notification": ["notification", "toast", "alert", "snackbar"],
    "search": ["search", "searchbar", "search-bar", "searchbox"],
    "settings": ["settings", "config", "preferences", "options-panel"],
    "markdown": ["markdown", "ansi-renderer", "rich-text"],
}

# ── Data Types ─────────────────────────────────────────────────────────────────


class DesignPattern:
    """A reusable design pattern extracted from code."""

    def __init__(
        self,
        pattern_type: str,  # "layout" | "interaction" | "styling" | "component" | "architectural" | "api"
        name: str,
        description: str,
        implementation: str,  # relative file path
        source: str,  # which repo this came from
        key_features: list[str] | None = None,
        code_snippet: str = "",
        relevance: float = 0.5,
        sub_patterns: list[str] | None = None,
    ):
        self.pattern_type = pattern_type
        self.name = name
        self.description = description
        self.implementation = implementation
        self.source = source
        self.key_features = key_features or []
        self.code_snippet = code_snippet
        self.relevance = relevance
        self.sub_patterns = sub_patterns or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "name": self.name,
            "description": self.description,
            "implementation": self.implementation,
            "source": self.source,
            "key_features": self.key_features,
            "code_snippet": self.code_snippet[:2000] if len(self.code_snippet) > 2000 else self.code_snippet,
            "relevance": self.relevance,
            "sub_patterns": self.sub_patterns,
        }

    def __repr__(self) -> str:
        return (
            f"<DesignPattern {self.pattern_type}:{self.name} "
            f"[{self.source}] rel={self.relevance:.2f}>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Core Module
# ═══════════════════════════════════════════════════════════════════════════════


class PatternExtractor:
    """Extract reusable design patterns from RepositoryKnowledge.

    This is a two-pass extractor:
    Pass 1: Scan all files for common patterns using heuristics.
    Pass 2: Refine and deduplicate extracted patterns.
    """

    # ── Public API ─────────────────────────────────────────────────────────

    @classmethod
    def extract(
        cls,
        knowledge: RepositoryKnowledge,
        focus_area: str = "",
    ) -> list[DesignPattern]:
        """Extract all reusable design patterns from a knowledge base.

        Args:
            knowledge: RepositoryKnowledge from CodeLearner.learn() or
                       CodeLearner.learn_from_local().
            focus_area: Optional focus hint to weight certain pattern types.

        Returns:
            List of DesignPattern objects, sorted by relevance.
        """
        all_patterns: list[DesignPattern] = []
        source = knowledge.source_name

        # Pass 1: Extract patterns by type
        all_patterns.extend(cls._extract_component_patterns(knowledge, source))
        all_patterns.extend(cls._extract_layout_patterns(knowledge, source))
        all_patterns.extend(cls._extract_interaction_patterns(knowledge, source))
        all_patterns.extend(cls._extract_styling_patterns(knowledge, source))
        all_patterns.extend(cls._extract_api_patterns(knowledge, source))
        all_patterns.extend(cls._extract_architectural_patterns(knowledge, source))

        # Pass 2: Deduplicate (same pattern_type + name from same source)
        all_patterns = cls._deduplicate(all_patterns)

        # Pass 3: Sort by relevance
        focus_lower = focus_area.lower()
        if "frontend" in focus_lower or "ui" in focus_lower or "gui" in focus_lower:
            # Boost component, layout, styling patterns
            for p in all_patterns:
                if p.pattern_type in ("component", "layout", "styling"):
                    p.relevance = min(1.0, p.relevance + 0.1)
        elif "backend" in focus_lower or "api" in focus_lower:
            for p in all_patterns:
                if p.pattern_type in ("api", "architectural"):
                    p.relevance = min(1.0, p.relevance + 0.1)

        all_patterns.sort(key=lambda p: p.relevance, reverse=True)

        logger.info(
            "Extracted %d patterns from %s",
            len(all_patterns), source,
        )
        for p in all_patterns[:10]:
            logger.debug("  %s: %s (%.2f)", p.pattern_type, p.name, p.relevance)

        return all_patterns

    @classmethod
    def filter_by_type(
        cls, patterns: list[DesignPattern], pattern_type: str
    ) -> list[DesignPattern]:
        """Filter patterns by type."""
        return [p for p in patterns if p.pattern_type == pattern_type]

    # ── Component Pattern Extraction ─────────────────────────────────────

    @classmethod
    def _extract_component_patterns(
        cls, knowledge: RepositoryKnowledge, source: str
    ) -> list[DesignPattern]:
        """Extract UI component patterns."""
        patterns: list[DesignPattern] = []

        for comp in knowledge.ui_components:
            comp_name = comp.get("name", "")
            comp_file = comp.get("file_path", "")
            comp_type = comp.get("widget_type", "unknown")
            features = comp.get("features", [])
            classes = comp.get("classes", [])
            lines = comp.get("lines", 0)

            # Skip unknown types
            if comp_type == "unknown":
                continue

            # Determine canonical pattern name
            pattern_name = cls._canonical_component_name(comp_name, comp_type)

            # Try to get code snippet
            code_snippet = cls._get_code_snippet(
                knowledge.local_path, comp_file,
            )

            # Build key features
            key_features = list(features)
            if lines > 50:
                key_features.append(f"{lines} 行代码")
            if classes:
                key_features.append(f"类: {', '.join(classes[:3])}")

            description = (
                f"{pattern_name} 组件定义在 {comp_file} "
                f"(类型: {comp_type}, {lines} 行)"
            )

            # Calculate relevance based on component complexity
            relevance = min(1.0, (len(features) * 0.1 + min(lines / 200, 0.5)))

            # Only include components with sufficient detail
            if relevance > 0.2:
                patterns.append(DesignPattern(
                    pattern_type="component",
                    name=pattern_name,
                    description=description,
                    implementation=comp_file,
                    source=source,
                    key_features=key_features,
                    code_snippet=code_snippet,
                    relevance=round(relevance, 2),
                    sub_patterns=[],
                ))

        # Also extract component patterns from file structure analysis
        for file_info in knowledge.files:
            path_lower = file_info.path.lower()
            file_stem = Path(file_info.path).stem.lower()

            # Check for component files that weren't caught by UI extraction
            for comp_type_keywords in (
                ("chat_bubble", ["bubble", "chatmsg", "message"]),
                ("input_box", ["input", "textinput", "chatinput"]),
                ("sidebar", ["sidebar", "sidenav"]),
                ("settings", ["setting", "configpanel", "preference"]),
                ("navbar", ["navbar", "nav-bar", "topbar", "header"]),
            ):
                canonical_name = comp_type_keywords[0]
                keywords = comp_type_keywords[1]

                if any(kw in path_lower or kw in file_stem for kw in keywords):
                    # Check it's not already included
                    already_have = any(
                        p.name.lower() == canonical_name and p.source == source
                        for p in patterns
                    )
                    if not already_have:
                        code_snippet = cls._get_code_snippet(
                            knowledge.local_path, file_info.path,
                        )
                        patterns.append(DesignPattern(
                            pattern_type="component",
                            name=canonical_name.replace("_", " ").title(),
                            description=f"{canonical_name.replace('_', ' ').title()} 组件在 {file_info.path}",
                            implementation=file_info.path,
                            source=source,
                            key_features=[f"{file_info.lines} 行"],
                            code_snippet=code_snippet,
                            relevance=0.4,
                        ))

        return patterns

    @classmethod
    def _canonical_component_name(
        cls, file_stem: str, widget_type: str
    ) -> str:
        """Map a filename and widget type to a canonical component name."""
        type_to_name: dict[str, str] = {
            "button": "按钮",
            "input": "输入框",
            "card": "卡片",
            "dialog": "对话框",
            "navigation": "导航栏",
            "list": "列表",
            "container": "容器",
            "display": "显示组件",
            "page": "页面",
            "layout": "布局",
            "unknown": file_stem,
        }

        base_name = type_to_name.get(widget_type, file_stem)
        # Use file stem as the specific instance
        readable = file_stem.replace("_", " ").replace("-", " ").title()
        return f"{readable} ({base_name})"

    # ── Layout Pattern Extraction ─────────────────────────────────────────

    @classmethod
    def _extract_layout_patterns(
        cls, knowledge: RepositoryKnowledge, source: str
    ) -> list[DesignPattern]:
        """Extract layout patterns from the codebase."""
        patterns: list[DesignPattern] = []

        for file_info in knowledge.files:
            path_lower = file_info.path.lower()
            file_stem = Path(file_info.path).stem.lower()
            content = file_info.content.lower()

            # Check for layout keywords in path or content
            matched_keywords = {
                kw for kw in LAYOUT_KEYWORDS if kw in path_lower or kw in file_stem
            }

            # Also check content for layout-related code
            content_layout_kws = {
                kw for kw in LAYOUT_KEYWORDS
                if len(kw) > 3 and kw in content
            }
            matched_keywords.update(content_layout_kws)

            if not matched_keywords:
                continue

            # Determine specific layout type
            layout_type = cls._determine_layout_type(
                matched_keywords, path_lower, content,
            )

            # Build description
            description = cls._build_layout_description(
                layout_type, file_info.path, matched_keywords,
            )

            # Build key features
            key_features: list[str] = []
            if "flex" in content or "grid" in content:
                key_features.append("响应式布局")
            if "collaps" in content or "collapse" in content:
                key_features.append("可折叠")
            if "scroll" in content:
                key_features.append("可滚动")
            if "resize" in content or "split" in content:
                key_features.append("可调整大小")
            if "drag" in content:
                key_features.append("可拖拽")

            code_snippet = cls._get_code_snippet(
                knowledge.local_path, file_info.path,
            )

            # Relevance based on keyword match density
            relevance = min(1.0, len(matched_keywords) * 0.15 + 0.2)

            if relevance > 0.25:
                patterns.append(DesignPattern(
                    pattern_type="layout",
                    name=layout_type,
                    description=description,
                    implementation=file_info.path,
                    source=source,
                    key_features=key_features,
                    code_snippet=code_snippet,
                    relevance=round(relevance, 2),
                ))

        return patterns

    @classmethod
    def _determine_layout_type(
        cls,
        keywords: set[str],
        path_lower: str,
        content: str,
    ) -> str:
        """Determine the specific layout pattern name."""
        keyword_to_layout: dict[str, str] = {
            "sidebar": "侧边栏布局",
            "nav": "导航布局",
            "navigation": "导航布局",
            "header": "顶部导航栏",
            "footer": "页脚布局",
            "tabs": "标签页布局",
            "accordion": "折叠面板布局",
            "grid": "网格布局",
            "flex": "弹性布局",
            "splitter": "分割面板布局",
            "split": "分割面板布局",
            "panel": "面板布局",
            "container": "容器布局",
            "layout": "通用布局",
            "dialog": "对话框布局",
            "modal": "模态框布局",
            "toolbar": "工具栏布局",
            "statusbar": "状态栏布局",
            "breadcrumb": "面包屑导航",
            "stacked": "堆叠页面布局",
        }

        for kw, name in keyword_to_layout.items():
            if kw in path_lower or kw in content:
                return name

        return "自定义布局"

    @classmethod
    def _build_layout_description(
        cls,
        layout_type: str,
        file_path: str,
        keywords: set[str],
    ) -> str:
        """Build a human-readable description for a layout pattern."""
        return (
            f"{layout_type} 实现于 {file_path}。"
            f"涉及关键词: {', '.join(sorted(keywords)[:6])}。"
            f"该布局定义了界面的空间组织和组件排列方式。"
        )

    # ── Interaction Pattern Extraction ──────────────────────────────────

    @classmethod
    def _extract_interaction_patterns(
        cls, knowledge: RepositoryKnowledge, source: str
    ) -> list[DesignPattern]:
        """Extract interaction patterns (click, keyboard, hover, etc.)."""
        patterns: list[DesignPattern] = []

        # Collect interaction keyword usage across all UI files
        interaction_usage: dict[str, list[str]] = defaultdict(list)

        for file_info in knowledge.files:
            content = file_info.content.lower()
            for kw in INTERACTION_KEYWORDS:
                if kw in content:
                    interaction_usage[kw].append(file_info.path)

        # Group into canonical interaction patterns
        pattern_mapping: list[tuple[str, list[str], str, list[str]]] = [
            (
                "键盘快捷键",
                ["keypress", "keydown", "keyup", "keyrelease", "onkeydown", "onkeypress", "onkeyup"],
                "通过键盘快捷键（如 Enter、Escape、Tab）触发操作",
                ["快捷操作", "无障碍", "键盘导航"],
            ),
            (
                "点击交互",
                ["click", "onclick", "clicked"],
                "通过鼠标点击触发的交互操作",
                ["按钮点击", "链接点击", "卡片点击"],
            ),
            (
                "滚动交互",
                ["scroll", "onscroll"],
                "鼠标或触控板滚动触发的交互",
                ["自动滚动", "无限加载", "粘性滚动"],
            ),
            (
                "拖拽交互",
                ["drag", "drop", "dragover", "dragstart"],
                "通过拖拽和放置触发的交互",
                ["拖拽排序", "文件拖放", "面板拖拽"],
            ),
            (
                "悬停交互",
                ["hover", "mouseenter", "mouseleave"],
                "鼠标悬停触发的视觉反馈",
                ["悬停高亮", "工具提示", "预览"],
            ),
            (
                "聚焦交互",
                ["focus", "blur", "onfocus", "onblur"],
                "输入框获得/失去焦点时的交互",
                ["输入提示", "验证反馈", "自动完成"],
            ),
            (
                "展开/折叠",
                ["expand", "collapse", "toggle"],
                "展开和折叠界面区域的交互",
                ["折叠面板", "展开详情", "切换开关"],
            ),
            (
                "表单输入",
                ["change", "input", "submit"],
                "表单输入和提交交互",
                ["实时验证", "自动保存", "提交确认"],
            ),
            (
                "动画过渡",
                ["animation", "transition", "transform"],
                "界面元素的动画和过渡效果",
                ["淡入淡出", "滑动动画", "变换效果"],
            ),
        ]

        for pattern_name, keywords, description, default_features in pattern_mapping:
            # Find all files that use any of the keywords
            files_using = set()
            for kw in keywords:
                files_using.update(interaction_usage.get(kw, []))

            if files_using:
                # Pick the most relevant implementation file
                impl_file = min(
                    files_using,
                    key=lambda f: (
                        len(f.split("/")),
                        -len(f),
                    ),  # prefer shorter paths (closer to root) then longer names
                )

                # Count how many of the keywords are found
                matched_keywords = [kw for kw in keywords if kw in interaction_usage]
                coverage = len(matched_keywords) / max(len(keywords), 1)

                relevance = round(min(1.0, coverage * 0.6 + 0.2), 2)

                patterns.append(DesignPattern(
                    pattern_type="interaction",
                    name=pattern_name,
                    description=(
                        f"{description}。在 {len(files_using)} 个文件中检测到，"
                        f"含 {len(matched_keywords)} 个相关关键词"
                    ),
                    implementation=impl_file,
                    source=source,
                    key_features=default_features,
                    code_snippet="",
                    relevance=relevance,
                ))

        return patterns

    # ── Styling Pattern Extraction ───────────────────────────────────────

    @classmethod
    def _extract_styling_patterns(
        cls, knowledge: RepositoryKnowledge, source: str
    ) -> list[DesignPattern]:
        """Extract styling patterns (theme, CSS, design tokens)."""
        patterns: list[DesignPattern] = []

        # Analyze design tokens
        tokens = knowledge.design_tokens
        if tokens:
            theme_files = tokens.get("theme_files", [])

            if theme_files:
                colors = list(set(tokens.get("colors", [])))
                spacing = list(set(tokens.get("spacing", [])))
                fonts = list(set(tokens.get("fonts", [])))
                radii = list(set(tokens.get("border_radii", [])))

                # Theme structure pattern
                if colors or spacing or fonts:
                    feature_parts = []
                    if colors:
                        feature_parts.append(f"{len(colors)} 个颜色值")
                    if spacing:
                        feature_parts.append(f"{len(spacing)} 个间距值")
                    if fonts:
                        feature_parts.append(f"{len(fonts)} 个字体定义")
                    if radii:
                        feature_parts.append(f"{len(radii)} 个圆角值")

                    patterns.append(DesignPattern(
                        pattern_type="styling",
                        name="设计主题系统",
                        description=(
                            f"结构化设计主题系统，包含 {', '.join(feature_parts)}。"
                            f"定义在 {', '.join(theme_files[:3])}"
                        ),
                        implementation=theme_files[0] if theme_files else "",
                        source=source,
                        key_features=feature_parts + ["统一主题管理", "可复用设计变量"],
                        code_snippet="",
                        relevance=0.75 if len(colors) > 5 else 0.5,
                    ))

        # Styling mechanism pattern
        for file_info in knowledge.files:
            path_lower = file_info.path.lower()
            content = file_info.content.lower()

            is_style_file = any(
                kw in path_lower or kw in file_info.path
                for kw in ("style", "theme", "css", "qss", "scss", "sass", "less")
            )
            if not is_style_file:
                continue

            # Determine styling approach
            if "qss" in content or "setstylesheet" in content:
                style_approach = "QSS (Qt样式表)"
            elif "css-in-js" in content or "styled." in content:
                style_approach = "CSS-in-JS"
            elif "scss" in path_lower:
                style_approach = "SCSS/Sass"
            elif "less" in path_lower:
                style_approach = "Less"
            else:
                style_approach = "CSS"

            patterns.append(DesignPattern(
                pattern_type="styling",
                name=f"{style_approach} 样式方案",
                description=(
                    f"使用 {style_approach} 管理样式。"
                    f"主要样式文件: {file_info.path} ({file_info.lines} 行)"
                ),
                implementation=file_info.path,
                source=source,
                key_features=[f"{file_info.lines} 行样式代码", style_approach],
                code_snippet=cls._get_code_snippet(
                    knowledge.local_path, file_info.path, max_chars=1000,
                ),
                relevance=0.6,
            ))

        return patterns

    # ── API Pattern Extraction ──────────────────────────────────────────

    @classmethod
    def _extract_api_patterns(
        cls, knowledge: RepositoryKnowledge, source: str
    ) -> list[DesignPattern]:
        """Extract API endpoint patterns."""
        patterns: list[DesignPattern] = []

        endpoints = knowledge.api_endpoints
        if not endpoints:
            return patterns

        # Group by base path
        path_groups: dict[str, list[dict]] = defaultdict(list)
        for ep in endpoints:
            path = ep.get("path", "")
            # Get the first segment as the resource group
            segments = path.strip("/").split("/")
            resource = segments[0] if segments else "root"
            path_groups[resource].append(ep)

        for resource, eps in path_groups.items():
            methods = [ep["method"] for ep in eps]
            paths = [ep["path"] for ep in eps]

            # Generate RESTful resource pattern
            resource_name = resource.replace("-", " ").replace("_", " ").title()
            method_list = "/".join(sorted(set(methods)))

            patterns.append(DesignPattern(
                pattern_type="api",
                name=f"{resource_name} REST API",
                description=(
                    f"RESTful API for {resource_name}: {method_list} "
                    f"({len(eps)} 个端点)。"
                    f"路径模式: {', '.join(paths[:5])}"
                ),
                implementation=eps[0].get("file", ""),
                source=source,
                key_features=[
                    f"{len(eps)} 个端点",
                    f"方法: {method_list}",
                    "RESTful 设计",
                ],
                code_snippet="",
                relevance=min(1.0, len(eps) * 0.15),
            ))

        return patterns

    # ── Architectural Pattern Extraction ─────────────────────────────────

    @classmethod
    def _extract_architectural_patterns(
        cls, knowledge: RepositoryKnowledge, source: str
    ) -> list[DesignPattern]:
        """Extract architectural patterns."""
        patterns: list[DesignPattern] = []

        arch_roles = knowledge.architecture_roles
        if not arch_roles:
            return patterns

        # Map roles to high-level patterns
        role_to_arch: dict[str, tuple[str, str, list[str]]] = {
            "ui_component": ("组件化架构", "前端组件化架构", ["组件封装", "props/state 管理", "组件复用"]),
            "ui_page": ("页面路由架构", "页面级路由和导航", ["路由管理", "页面切换", "懒加载"]),
            "ui_layout": ("布局系统", "可复用布局系统", ["布局嵌套", "响应式适配"]),
            "api_endpoint": ("API 分层架构", "前后端 API 分层", ["路由定义", "Handler 分离"]),
            "api_controller": ("控制器模式", "控制器层负责请求处理", ["请求验证", "响应格式化"]),
            "data_model": ("数据模型层", "领域模型和数据定义", ["ORM 模型", "数据验证", "序列化"]),
            "data_access": ("数据访问层", "封装数据库查询", ["SQL 抽象", "连接池", "事务管理"]),
            "core_module": ("核心模块", "核心业务逻辑封装", ["单例模式", "模块化"]),
            "service": ("服务层", "业务服务编排", ["服务注入", "事务边界"]),
            "configuration": ("配置管理", "集中式配置管理", ["环境变量", "配置文件", "运行时配置"]),
            "test": ("测试架构", "测试分层和工具", ["单元测试", "集成测试", "Mock"]),
        }

        for role, count in arch_roles.items():
            if role in role_to_arch and count >= 1:
                name, description, features = role_to_arch[role]
                patterns.append(DesignPattern(
                    pattern_type="architectural",
                    name=name,
                    description=(
                        f"{description} — {count} 个目录/模块。"
                        f"来源: {source}"
                    ),
                    implementation="",
                    source=source,
                    key_features=features + [f"{count} 个模块"],
                    code_snippet="",
                    relevance=min(1.0, count * 0.15 + 0.3),
                ))

        # Source file layout pattern
        if knowledge.file_count > 0:
            dir_count = knowledge.dir_count
            if dir_count > 3:
                patterns.append(DesignPattern(
                    pattern_type="architectural",
                    name="目录结构组织",
                    description=(
                        f"{dir_count} 个目录, {knowledge.file_count} 个文件的"
                        f"层次化目录结构 ({knowledge.primary_language})"
                    ),
                    implementation="",
                    source=source,
                    key_features=[
                        f"{dir_count} 个目录",
                        f"{knowledge.file_count} 个文件",
                        f"主语言: {knowledge.primary_language}",
                    ],
                    code_snippet="",
                    relevance=0.5,
                ))

        return patterns

    # ── Utility Methods ────────────────────────────────────────────────────

    @classmethod
    def _deduplicate(
        cls, patterns: list[DesignPattern]
    ) -> list[DesignPattern]:
        """Remove duplicate patterns (same type + name + source)."""
        seen: set[tuple[str, str, str]] = set()
        unique: list[DesignPattern] = []

        for p in sorted(patterns, key=lambda x: x.relevance, reverse=True):
            key = (p.pattern_type, p.name, p.source)
            if key not in seen:
                seen.add(key)
                unique.append(p)

        return unique

    @classmethod
    def _get_code_snippet(
        cls, repo_path: str, file_path: str, max_chars: int = 2000
    ) -> str:
        """Get a code snippet from a file in the repo."""
        if not repo_path or not file_path:
            return ""

        full_path = os.path.join(repo_path, file_path)
        if not os.path.isfile(full_path):
            return ""

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars)
            return content
        except Exception:
            return ""

    @classmethod
    def patterns_to_json(
        cls, patterns: list[DesignPattern]
    ) -> list[dict[str, Any]]:
        """Convert all patterns to JSON-serializable dicts."""
        return [p.to_dict() for p in patterns]
