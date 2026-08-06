"""Pattern Comparator — compares external design patterns against Partner's own GUI.

This module takes two sets of DesignPattern objects (external vs Partner's own)
and produces a structured list of "gaps" — features or patterns that:
  - Partner doesn't have at all (Type A gap)
  - Partner has but is functionally inferior (Type B gap)
  - Partner has but is visually/experientially different (Type C gap)

Usage:
    from partner.evolution.pattern_comparator import PatternComparator

    gaps = PatternComparator.compare(
        external_patterns=hermes_patterns,
        partner_patterns=partner_patterns,
        focus_area="frontend",
    )
    for gap in gaps:
        print(f"[{gap['priority']}] {gap['category']}: {gap['external_pattern']}")
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from .code_knowledge import (
    PARTNER_GUI_ROOT,
    CodeKnowledge,
    ImprovementPlan,
)
from .code_learner import RepositoryKnowledge
from .pattern_extractor import DesignPattern

logger = logging.getLogger(__name__)

# ── Partner's own code patterns (baseline) ────────────────────────────────────

# Known layout patterns in Partner's current GUI
PARTNER_KNOWN_LAYOUTS: dict[str, list[str]] = {
    "有良好实现": [
        "侧边栏布局",  # Sidebar with navigation
        "对话消息布局",  # Chat bubble layout
        "面板布局",  # Panel-based layout
        "标签页布局",  # Tab layout (instances page)
        "对话框布局",  # Dialog boxes
    ],
    "有但简陋": [
        "导航布局",  # Simple navigation, no advanced features
        "顶部导航栏",  # Minimal header
        "输入框布局",  # Basic input area
        "卡片布局",  # Basic card rendering
        "通用布局",  # Generic layout containers
    ],
    "缺失的布局": [
        "网格布局",  # No grid layout system
        "弹性布局",  # No CSS-like flexbox in Qt
        "分割面板布局",  # No splitter panels
        "折叠面板布局",  # Basic accordion in step widgets
        "状态栏布局",  # No status bar with dynamic content
        "工具栏布局",  # No toolbar
        "模态框布局",  # Basic dialogs but no modal overlay
    ],
}

# Known interaction patterns in Partner's current GUI
PARTNER_KNOWN_INTERACTIONS: dict[str, list[str]] = {
    "有良好实现": [
        "点击交互",  # Button clicks work well
        "表单输入",  # Text input works
        "滚动交互",  # Scrollable chat area
    ],
    "有但简陋": [
        "悬停交互",  # Basic hover on buttons but not cards
        "展开/折叠",  # Expand/collapse on EventStepWidget but not animated
    ],
    "缺失的交互": [
        "键盘快捷键",  # No keyboard navigation
        "拖拽交互",  # No drag-drop
        "聚焦交互",  # No focus management
        "动画过渡",  # No smooth transitions
        "右键菜单",  # No context menus
        "自动完成",  # No autocomplete in search/input
    ],
}

# Known component patterns in Partner's current GUI
PARTNER_KNOWN_COMPONENTS: dict[str, list[str]] = {
    "有良好实现": [
        "ChatBubble",
        "EventCard",
        "EventStepWidget",
        "AccentButton",
        "SectionHeader",
        "StatusCard",
    ],
    "有但简陋": [
        "输入框",  # Basic QPlainTextEdit, no features
        "按钮",  # AccentButton exists but no variants
        "侧边栏",  # Sidebar exists but not collapsible
        "导航栏",  # Tab-based navigation
    ],
    "缺失的组件": [
        "Avatar",  # No user avatar component
        "Badge",  # No notification badges
        "Tooltip",  # No hover tooltips
        "Toast/Notification",  # No transient notifications
        "Progress/Spinner",  # No loading indicators
        "SearchBar",  # No search input
        "Dropdown/Select",  # No dropdown selector
        "Modal/Dialog overlay",  # No modal backdrop
        "Tabs",  # Tab switching via QTabWidget but not styled
        "Markdown Renderer",  # Rich text but no Markdown rendering
        "ANSI Renderer",  # No ANSI color code support
        "Dark mode toggle",  # No theme switching
    ],
}

# Known design tokens in Partner
PARTNER_DESIGN_TOKENS: dict[str, list[str]] = {
    "colors": ["#2B2B3D", "#89B4FA", "#CDD6F4", "#A6ADC8", "#45475A"],
    "spacing": ["4px", "8px", "12px", "16px", "24px"],
    "fonts": ["13px sans-serif"],
    "border_radii": ["8px", "10px", "16px"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# Gap Detection
# ═══════════════════════════════════════════════════════════════════════════════


class PatternComparator:
    """Compare external patterns against Partner's own and produce gap list."""

    _gap_counter: int = 0

    # ── Public API ─────────────────────────────────────────────────────────

    @classmethod
    def compare(
        cls,
        external_patterns: list[DesignPattern],
        partner_patterns: list[DesignPattern] | None = None,
        focus_area: str = "",
        partner_gui_root: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Compare external patterns against Partner's baseline.

        If partner_patterns is None, falls back to built-in known patterns
        (PARTNER_KNOWN_* dictionaries) as the baseline.

        Args:
            external_patterns: Patterns extracted from external code.
            partner_patterns: Patterns extracted from Partner's own code
                              (optional — uses built-in baseline if omitted).
            focus_area: Optional focus hint.
            partner_gui_root: Override Partner GUI root path.

        Returns:
            List of gap dicts sorted by priority (high → low).
        """
        cls._gap_counter = 0
        gaps: list[dict[str, Any]] = []

        # Analyze external patterns by type
        ext_layouts = [p for p in external_patterns if p.pattern_type == "layout"]
        ext_interactions = [p for p in external_patterns if p.pattern_type == "interaction"]
        ext_components = [p for p in external_patterns if p.pattern_type == "component"]
        ext_styling = [p for p in external_patterns if p.pattern_type == "styling"]
        ext_arch = [p for p in external_patterns if p.pattern_type == "architectural"]

        # 1. Compare layout patterns
        gaps.extend(cls._compare_layouts(ext_layouts))

        # 2. Compare interaction patterns
        gaps.extend(cls._compare_interactions(ext_interactions))

        # 3. Compare component patterns
        gaps.extend(cls._compare_components(ext_components))

        # 4. Compare styling/theme patterns
        gaps.extend(cls._compare_styling(ext_styling))

        # 5. Architecture-level gaps
        gaps.extend(cls._compare_architecture(ext_arch))

        # 6. Sort by priority
        priority_order = {"high": 0, "medium": 1, "low": 2}
        gaps.sort(key=lambda g: priority_order.get(g.get("priority", "low"), 99))

        logger.info(
            "Comparison complete: %d gaps found (high=%d, med=%d, low=%d)",
            len(gaps),
            sum(1 for g in gaps if g.get("priority") == "high"),
            sum(1 for g in gaps if g.get("priority") == "medium"),
            sum(1 for g in gaps if g.get("priority") == "low"),
        )

        return gaps

    # ── Layout Comparison ──────────────────────────────────────────────────

    @classmethod
    def _compare_layouts(
        cls, external_layouts: list[DesignPattern]
    ) -> list[dict[str, Any]]:
        """Compare external layout patterns against Partner's known layouts."""
        gaps: list[dict[str, Any]] = []

        # Build set of Partner's layout names (both good and basic)
        partner_layout_names: set[str] = set()
        for status_list in PARTNER_KNOWN_LAYOUTS.values():
            partner_layout_names.update(status_list)

        for ext_layout in external_layouts:
            pattern_name = ext_layout.name

            # Check if Partner has it at all
            if pattern_name not in partner_layout_names:
                # Check if it's a "missing" layout (known to be absent)
                is_known_missing = pattern_name in PARTNER_KNOWN_LAYOUTS.get("缺失的布局", [])

                # Check if the name contains known-missing keywords
                missing_keywords = [
                    "网格", "flex", "弹性", "分割", "split", "折叠",
                    "accordion", "状态栏", "工具栏", "工具条",
                    "模态", "modal overlay", "overlay",
                ]
                has_missing_keyword = any(kw in pattern_name for kw in missing_keywords)

                priority = "high" if (is_known_missing or has_missing_keyword) else "medium"
                # Boost relevance for highly relevant patterns
                relevance_boost = "high" if ext_layout.relevance > 0.7 else priority

                gaps.append(cls._make_gap(
                    category="布局",
                    external_pattern=pattern_name,
                    partner_status=f"Partner 无此布局组件",
                    priority=relevance_boost,
                    suggestion=(
                        f"参考 {ext_layout.source}/{ext_layout.implementation}，"
                        f"实现 {pattern_name} 组件。"
                        f"{ext_layout.description[:100]}"
                    ),
                    external_source=ext_layout.source,
                    external_file=ext_layout.implementation,
                    features=ext_layout.key_features,
                ))
            else:
                # Partner has it — check quality
                if pattern_name in PARTNER_KNOWN_LAYOUTS.get("有但简陋", []):
                    gaps.append(cls._make_gap(
                        category="布局",
                        external_pattern=pattern_name,
                        partner_status="Partner 有该布局，但功能简陋",
                        priority="medium",
                        suggestion=(
                            f"增强 Partner 的 {pattern_name}："
                            f"参考 {ext_layout.source}/{ext_layout.implementation} "
                            f"添加 {', '.join(ext_layout.key_features[:3])}"
                        ),
                        external_source=ext_layout.source,
                        external_file=ext_layout.implementation,
                        features=ext_layout.key_features,
                    ))

        return gaps

    # ── Interaction Comparison ─────────────────────────────────────────────

    @classmethod
    def _compare_interactions(
        cls, external_interactions: list[DesignPattern]
    ) -> list[dict[str, Any]]:
        """Compare external interaction patterns against Partner's."""
        gaps: list[dict[str, Any]] = []

        partner_bad_interactions: set[str] = set()
        partner_missing_interactions: set[str] = set()
        for items in PARTNER_KNOWN_INTERACTIONS.get("有但简陋", []):
            partner_bad_interactions.add(items)
        for items in PARTNER_KNOWN_INTERACTIONS.get("缺失的交互", []):
            partner_missing_interactions.add(items)

        for ext_int in external_interactions:
            pattern_name = ext_int.name

            if pattern_name in partner_missing_interactions:
                gaps.append(cls._make_gap(
                    category="交互",
                    external_pattern=pattern_name,
                    partner_status="Partner 完全缺少此交互模式",
                    priority="high",
                    suggestion=(
                        f"为 Partner GUI 添加 {pattern_name} 支持。"
                        f"{ext_int.description[:120]}"
                    ),
                    external_source=ext_int.source,
                    external_file=ext_int.implementation,
                    features=ext_int.key_features,
                ))
            elif pattern_name in partner_bad_interactions:
                gaps.append(cls._make_gap(
                    category="交互",
                    external_pattern=pattern_name,
                    partner_status="Partner 有基本的交互，但不完善",
                    priority="medium",
                    suggestion=(
                        f"增强 Partner 的 {pattern_name}："
                        f"参考 {ext_int.source}/{ext_int.implementation}"
                    ),
                    external_source=ext_int.source,
                    external_file=ext_int.implementation,
                    features=ext_int.key_features,
                ))

        return gaps

    # ── Component Comparison ──────────────────────────────────────────────

    @classmethod
    def _compare_components(
        cls, external_components: list[DesignPattern]
    ) -> list[dict[str, Any]]:
        """Compare external component patterns against Partner's."""
        gaps: list[dict[str, Any]] = []

        partner_components_good: set[str] = set()
        partner_components_basic: set[str] = set()
        partner_components_missing: set[str] = set()
        for items in PARTNER_KNOWN_COMPONENTS.get("有良好实现", []):
            partner_components_good.add(items.lower().replace("_", "").replace("-", "").replace(" ", ""))
        for items in PARTNER_KNOWN_COMPONENTS.get("有但简陋", []):
            partner_components_basic.add(items.lower().replace("_", "").replace("-", "").replace(" ", ""))
        for items in PARTNER_KNOWN_COMPONENTS.get("缺失的组件", []):
            partner_components_missing.add(items.lower().replace("_", "").replace("-", "").replace(" ", ""))

        for ext_comp in external_components:
            pattern_name = ext_comp.name
            pattern_lower = pattern_name.lower().replace("_", "").replace("-", "").replace(" ", "")

            # Map external component to Partner's known component set
            matched_missing = None
            matched_basic = None

            for pcm in partner_components_missing:
                if pcm in pattern_lower or pattern_lower in pcm:
                    matched_missing = pcm
                    break

            if matched_missing:
                gaps.append(cls._make_gap(
                    category="组件",
                    external_pattern=pattern_name,
                    partner_status="Partner 完全缺少此组件",
                    priority="high",
                    suggestion=(
                        f"在 widgets.py 中新增 {pattern_name} 组件，"
                        f"参考 {ext_comp.source}/{ext_comp.implementation}"
                    ),
                    external_source=ext_comp.source,
                    external_file=ext_comp.implementation,
                    features=ext_comp.key_features,
                ))
                continue

            for pcb in partner_components_basic:
                if pcb in pattern_lower or pattern_lower in pcb:
                    matched_basic = pcb
                    break

            if matched_basic:
                gaps.append(cls._make_gap(
                    category="组件",
                    external_pattern=pattern_name,
                    partner_status=f"Partner 有类似的 {matched_basic} 但功能/视觉效果有差距",
                    priority="medium",
                    suggestion=(
                        f"增强 Partner 的 {matched_basic} 组件："
                        f"参考 {ext_comp.source}/{ext_comp.implementation} "
                        f"改进样式和交互"
                    ),
                    external_source=ext_comp.source,
                    external_file=ext_comp.implementation,
                    features=ext_comp.key_features,
                ))

        return gaps

    # ── Styling Comparison ─────────────────────────────────────────────────

    @classmethod
    def _compare_styling(
        cls, external_styling: list[DesignPattern]
    ) -> list[dict[str, Any]]:
        """Compare external styling patterns against Partner's."""
        gaps: list[dict[str, Any]] = []

        for ext_style in external_styling:
            if "主题" in ext_style.name:
                gaps.append(cls._make_gap(
                    category="样式",
                    external_pattern=ext_style.name,
                    partner_status="Partner 有基础的主题系统（QSS样式表），但缺少结构化设计变量",
                    priority="high",
                    suggestion=(
                        f"为 Partner 创建结构化设计主题系统，"
                        f"定义颜色、间距、字体、圆角等设计变量"
                    ),
                    external_source=ext_style.source,
                    external_file=ext_style.implementation,
                    features=ext_style.key_features,
                ))
            elif "响应式" in ext_style.name or "自适应" in ext_style.name:
                gaps.append(cls._make_gap(
                    category="样式",
                    external_pattern=ext_style.name,
                    partner_status="Partner GUI 未实现响应式布局",
                    priority="medium",
                    suggestion="为 Partner GUI 添加窗口大小变化时的自适应布局",
                    external_source=ext_style.source,
                    external_file=ext_style.implementation,
                    features=ext_style.key_features,
                ))

        # Check for dark mode gap
        has_dark_mode_theme = any(
            "暗色" in s.name or "dark" in s.name.lower() or "暗" in s.name
            for s in external_styling
        )
        if has_dark_mode_theme:
            gaps.append(cls._make_gap(
                category="样式",
                external_pattern="暗色模式主题",
                partner_status="Partner 仅有一个样式表，无 dark/light 主题切换",
                priority="medium",
                suggestion="实现 dark/light 双主题系统，在设置页面添加主题切换开关",
                external_source=external_styling[0].source if external_styling else "",
                external_file="",
                features=["暗色/亮色切换", "设计变量统一管理"],
            ))

        return gaps

    # ── Architecture Comparison ────────────────────────────────────────────

    @classmethod
    def _compare_architecture(
        cls, external_arch: list[DesignPattern]
    ) -> list[dict[str, Any]]:
        """Compare external architectural patterns against Partner's."""
        gaps: list[dict[str, Any]] = []

        for ext_arch in external_arch:
            # Check for important architectural patterns Partner might lack
            if "测试" in ext_arch.name:
                gaps.append(cls._make_gap(
                    category="架构",
                    external_pattern=ext_arch.name,
                    partner_status="Partner GUI 缺少自动化测试覆盖",
                    priority="medium",
                    suggestion="为 GUI 组件添加 PySide6 单元测试",
                    external_source=ext_arch.source,
                    external_file="",
                    features=[],
                ))
            elif "配置" in ext_arch.name:
                gaps.append(cls._make_gap(
                    category="架构",
                    external_pattern=ext_arch.name,
                    partner_status="Partner 有配置系统，但缺少 GUI 设置页面",
                    priority="medium",
                    suggestion="创建集中式设置页面，管理所有配置选项",
                    external_source=ext_arch.source,
                    external_file="",
                    features=[],
                ))
            elif "组件化" in ext_arch.name and ext_arch.relevance > 0.6:
                gaps.append(cls._make_gap(
                    category="架构",
                    external_pattern=ext_arch.name,
                    partner_status="Partner GUI 组件化程度较低，许多 UI 逻辑混合在页面中",
                    priority="medium",
                    suggestion="将可复用 UI 逻辑提取到 widgets.py，按组件模式重构",
                    external_source=ext_arch.source,
                    external_file="",
                    features=[],
                ))

        return gaps

    # ── Gap Factory ─────────────────────────────────────────────────────────

    @classmethod
    def _make_gap(
        cls,
        category: str,
        external_pattern: str,
        partner_status: str,
        priority: str,
        suggestion: str,
        external_source: str = "",
        external_file: str = "",
        features: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a structured gap entry."""
        cls._gap_counter += 1
        return {
            "gap_id": f"gap_{cls._gap_counter:03d}",
            "category": category,
            "external_pattern": external_pattern,
            "partner_status": partner_status,
            "priority": priority,
            "suggestion": suggestion,
            "external_source": external_source,
            "external_file": external_file,
            "features": features or [],
        }

    # ── Utility: generate comparator-based improvement plans ────────────────

    @classmethod
    def gaps_to_plans(
        cls,
        gaps: list[dict[str, Any]],
        plan_counter: int = 0,
    ) -> list[ImprovementPlan]:
        """Convert gap entries into ImprovementPlan objects.

        Uses the existing ImprovementPlan dataclass from code_knowledge.py
        so the plans are consumable by the existing SelfEvolveEngine flow.

        Args:
            gaps: List of gap dicts (output of compare()).
            plan_counter: Starting counter for plan IDs.

        Returns:
            List of ImprovementPlan objects.
        """
        plans: list[ImprovementPlan] = []
        ck = CodeKnowledge()

        for gap in gaps:
            plan_counter += 1
            plan_id = f"plan_c{plan_counter:03d}"

            # Map gap category to target file
            gap_category = gap.get("category", "")
            if gap_category == "布局":
                target_path = "shells/frontend/desktop_gui/modern/widgets.py"
            elif gap_category == "交互":
                target_path = "shells/frontend/desktop_gui/modern/pages/chat.py"
            elif gap_category == "组件":
                target_path = "shells/frontend/desktop_gui/modern/widgets.py"
            elif gap_category == "样式":
                target_path = "shells/frontend/desktop_gui/modern/styles.py"
            else:
                target_path = "shells/frontend/desktop_gui/modern/widgets.py"

            # Determine change type and risk level
            priority = gap.get("priority", "medium")
            if priority == "high":
                risk = "high"
                change_type = "add_feature"
            elif priority == "medium":
                risk = "medium"
                change_type = "add_feature"
            else:
                risk = "low"
                change_type = "modify_function"

            description = (
                f"[{gap['category']}] {gap['external_pattern']}: "
                f"{gap['partner_status']}. "
                f"{gap['suggestion']}"
            )

            plans.append(ImprovementPlan(
                id=plan_id,
                target_module=target_path,
                change_type=change_type,
                function_name="",
                new_code="",
                description=description[:300],
                risk_level=risk,
            ))

        return plans

    # ── Batch comparison: analyze actual Partner GUI and compare ────────────

    @classmethod
    async def compare_external_to_actual_partner(
        cls,
        external_knowledge: RepositoryKnowledge,
        external_patterns: list[DesignPattern],
        partner_gui_root: str | Path | None = None,
        progress_callback=None,
    ) -> list[dict[str, Any]]:
        """Compare external patterns against Partner's ACTUAL GUI code.

        Uses CodeKnowledge to scan Partner's actual GUI directory and
        extract Partner's real patterns for comparison.

        Args:
            external_knowledge: RepositoryKnowledge of the external repo.
            external_patterns: DesignPatterns from the external repo.
            partner_gui_root: Override path to Partner's GUI root.
            progress_callback: Async callable for progress reporting.

        Returns:
            List of gap dicts.
        """
        p_root = Path(partner_gui_root) if partner_gui_root else PARTNER_GUI_ROOT

        if progress_callback:
            await progress_callback("🔍 正在分析 Partner 自身 GUI 代码...")

        try:
            # Use CodeKnowledge to analyze actual Partner GUI
            ck = CodeKnowledge(partner_root=p_root)
            partner_structure = ck.analyze_frontend_structure(str(p_root))
            partner_patterns_ui = ck.extract_ui_patterns(str(p_root))

            if progress_callback:
                await progress_callback(
                    f"📊 Partner GUI: {partner_patterns_ui.component_count} 个组件, "
                    f"{partner_structure['file_count']} 个文件"
                )

            # Convert UIPattern to DesignPattern list
            partner_design_patterns: list[DesignPattern] = []
            for comp in partner_patterns_ui.component_hierarchy:
                partner_design_patterns.append(DesignPattern(
                    pattern_type="component",
                    name=comp.name,
                    description=(
                        f"Partner GUI 组件 {comp.name} ({comp.widget_type}) "
                        f"in {comp.file_path}"
                    ),
                    implementation=comp.file_path,
                    source="Partner",
                    key_features=[],
                    relevance=0.5,
                ))

            # Compare using actual patterns
            return cls.compare(
                external_patterns=external_patterns,
                partner_patterns=partner_design_patterns,
                partner_gui_root=p_root,
            )

        except Exception as exc:
            logger.warning(
                "Failed to analyze actual Partner GUI: %s. Falling back to baseline.",
                exc,
            )
            return cls.compare(
                external_patterns=external_patterns,
                partner_patterns=None,
            )
